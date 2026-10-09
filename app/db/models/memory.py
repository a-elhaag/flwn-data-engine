"""Memory Steward, Decision Ledger, agent runs and reports, search chunks, audit log.

GitHub owns repositories and pull requests: we store only the report an agent produced, with the
link inside it. A memory's history (recalled, revised, faded) goes in `events`, not its own table.

Human-like memory: kinds (episode, fact, decision, procedure...), a strength that decays and is
reinforced by recall, a confidence, associations between memories, and gradual fading.
"""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Float,
    ForeignKeyConstraint,
    Index,
    Numeric,
    SmallInteger,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    EMPTY_JSON,
    EMPTY_LIST,
    Base,
    Created,
    IdPk,
    Stamps,
    Tenant,
    one_of,
    tenant_unique,
    tfk,
)

# Must match the embedding deployment's output dimension (embed-v-4-0 defaults to 1536).
EMBEDDING_DIM = 1536
TSV = (
    "to_tsvector('simple', coalesce(text, ''))"  # 'simple': no stemming, works for mixed languages
)

MEMORY_KINDS = ("episode", "fact", "decision", "procedure", "ownership", "constraint", "insight")


class Memory(IdPk, Tenant, Stamps, Base):
    __tablename__ = "memories"

    kind: Mapped[str]
    scope: Mapped[str] = mapped_column(server_default="workspace")
    team_id: Mapped[uuid.UUID | None]
    project_id: Mapped[uuid.UUID | None]
    owner_member_id: Mapped[uuid.UUID | None]  # set for private agent memory
    text: Mapped[str]  # the distilled memory
    raw_text: Mapped[str | None] = mapped_column(deferred=True)  # what it was distilled from
    importance: Mapped[int] = mapped_column(
        SmallInteger, server_default="3"
    )  # 1-5, sets decay rate
    strength: Mapped[float] = mapped_column(
        Float, server_default="1"
    )  # rises on recall, decays with time
    confidence: Mapped[float] = mapped_column(Float, server_default="0.8")  # 0-1
    pinned: Mapped[bool] = mapped_column(
        Boolean, server_default="false"
    )  # core memories never fade
    status: Mapped[str] = mapped_column(server_default="active")
    superseded_by: Mapped[uuid.UUID | None]
    superseded_at: Mapped[datetime | None]
    recall_count: Mapped[int] = mapped_column(server_default="0")
    last_recalled_at: Mapped[datetime | None]
    source_type: Mapped[str | None]  # label such as chat, meeting, decision, code
    source_id: Mapped[uuid.UUID | None]  # the message, meeting or task it came from, when known
    source_member_id: Mapped[uuid.UUID | None]  # who said or decided it
    agent_name: Mapped[str | None]  # the agent that stored it
    refreshed_at: Mapped[datetime | None]  # set when a duplicate write re-confirms the memory
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), deferred=True)
    embedding_model: Mapped[str | None]
    tsv: Mapped[str | None] = mapped_column(TSVECTOR, Computed(TSV, persisted=True))
    created_by: Mapped[uuid.UUID | None]

    __table_args__ = (
        tenant_unique(),
        one_of("kind", *MEMORY_KINDS),
        one_of("scope", "workspace", "team", "project", "agent"),
        one_of("status", "active", "faded", "superseded"),
        CheckConstraint("importance between 1 and 5", name="importance_range"),
        CheckConstraint("confidence between 0 and 1", name="confidence_range"),
        CheckConstraint(
            "scope = 'workspace'"
            " or (scope = 'team' and team_id is not null)"
            " or (scope = 'project' and project_id is not null)"
            " or (scope = 'agent' and owner_member_id is not null)",
            name="scope_target",
        ),
        tfk("team_id", "teams", "cascade"),
        tfk("project_id", "projects", "cascade"),
        tfk("owner_member_id", "members", "cascade"),
        tfk("superseded_by", "memories", "set null"),
        tfk("source_member_id", "members", "set null"),
        tfk("created_by", "members", "set null"),
        Index("ix_memories_scope", "workspace_id", "kind", "status"),
        Index("ix_memories_project", "project_id", postgresql_where=text("project_id is not null")),
        Index(
            "ix_memories_source",
            "source_type",
            "source_id",
            postgresql_where=text("source_id is not null"),
        ),
        Index("ix_memories_tsv", "tsv", postgresql_using="gin"),
        Index(
            "ix_memories_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class Decision(Tenant, Stamps, Base):
    """Structured detail for a decision memory; Decision Ledger checks new work against these."""

    __tablename__ = "decisions"

    memory_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    statement: Mapped[str]
    rationale: Mapped[str | None]
    alternatives: Mapped[dict] = mapped_column(server_default=EMPTY_JSON)  # options considered
    area: Mapped[str | None]  # module or topic, e.g. "auth", "database"
    scope_paths: Mapped[list] = mapped_column(server_default=EMPTY_LIST)  # files or paths it covers
    status: Mapped[str] = mapped_column(server_default="active")
    owner_member_id: Mapped[uuid.UUID | None]
    work_item_id: Mapped[uuid.UUID | None]
    task_id: Mapped[uuid.UUID | None]
    decided_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    __table_args__ = (
        UniqueConstraint("workspace_id", "memory_id"),  # lets decision_conflicts reference it
        one_of("status", "proposed", "active", "superseded", "rejected"),
        tfk("memory_id", "memories", "cascade"),
        tfk("owner_member_id", "members", "set null"),
        tfk("work_item_id", "work_items", "set null"),
        tfk("task_id", "tasks", "set null"),
        Index("ix_decisions_status_area", "workspace_id", "status", "area"),
    )


class MemoryLink(Tenant, Created, Base):
    """Associations: derived_from, supports, contradicts, about, supersedes, related."""

    __tablename__ = "memory_links"

    from_memory_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    to_memory_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(primary_key=True)
    weight: Mapped[float] = mapped_column(Float, server_default="1")

    __table_args__ = (
        one_of("type", "derived_from", "supports", "contradicts", "about", "supersedes", "related"),
        CheckConstraint("from_memory_id <> to_memory_id", name="distinct_memories"),
        tfk("from_memory_id", "memories", "cascade"),
        tfk("to_memory_id", "memories", "cascade"),
        Index("ix_memory_links_to_memory_id", "to_memory_id"),
    )


class Chunk(IdPk, Tenant, Stamps, Base):
    """One searchable piece of any source (file page, doc, task, comment, message, transcript).

    A single retrieval table keeps recall simple: one hybrid query across everything.
    Private channels: set channel_id so recall can honor channel membership.
    """

    __tablename__ = "chunks"

    source_type: Mapped[str]
    source_id: Mapped[uuid.UUID]
    chunk_index: Mapped[int] = mapped_column(server_default="0")
    text: Mapped[str]
    heading_path: Mapped[str | None]  # "Design > Auth > Tokens", kept for citations
    page: Mapped[int | None]
    start_offset: Mapped[int | None]
    end_offset: Mapped[int | None]
    team_id: Mapped[uuid.UUID | None]
    project_id: Mapped[uuid.UUID | None]
    channel_id: Mapped[uuid.UUID | None]
    content_hash: Mapped[str | None]  # skip re-embedding unchanged chunks
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), deferred=True)
    embedding_model: Mapped[str | None]
    tsv: Mapped[str | None] = mapped_column(TSVECTOR, Computed(TSV, persisted=True))
    metadata_: Mapped[dict] = mapped_column("metadata", server_default=EMPTY_JSON)

    __table_args__ = (
        one_of(
            "source_type",
            "file",
            "doc",
            "task",
            "work_item",
            "comment",
            "message",
            "transcript",
            "report",
        ),
        UniqueConstraint("source_type", "source_id", "chunk_index"),
        tfk("team_id", "teams", "cascade"),
        tfk("project_id", "projects", "cascade"),
        tfk("channel_id", "channels", "cascade"),
        Index("ix_chunks_scope", "workspace_id", "source_type", "project_id"),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
        Index(
            "ix_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


# ------------------------------------------------------------ agents


class AgentRun(IdPk, Tenant, Created, Base):
    """One invocation of an agent (Ghost Engineer, Decision Ledger, ...)."""

    __tablename__ = "agent_runs"

    agent_id: Mapped[uuid.UUID]  # an AI member
    trigger: Mapped[str]
    triggered_by: Mapped[uuid.UUID | None]
    project_id: Mapped[uuid.UUID | None]
    work_item_id: Mapped[uuid.UUID | None]
    task_id: Mapped[uuid.UUID | None]
    channel_id: Mapped[uuid.UUID | None]
    meeting_id: Mapped[uuid.UUID | None]
    thread_id: Mapped[str | None]  # LangGraph thread
    status: Mapped[str] = mapped_column(server_default="queued")
    input: Mapped[dict] = mapped_column(server_default=EMPTY_JSON)
    output: Mapped[dict | None]
    error: Mapped[str | None]
    model: Mapped[str | None]
    started_at: Mapped[datetime | None]
    ended_at: Mapped[datetime | None]

    __table_args__ = (
        tenant_unique(),
        one_of("trigger", "chat", "ticket_event", "schedule", "user", "meeting", "webhook"),
        one_of(
            "status", "queued", "running", "waiting_approval", "succeeded", "failed", "canceled"
        ),
        tfk("agent_id", "members", "cascade"),
        tfk("triggered_by", "members", "set null"),
        tfk("project_id", "projects", "set null"),
        tfk("work_item_id", "work_items", "set null"),
        tfk("task_id", "tasks", "set null"),
        tfk("channel_id", "channels", "set null"),
        tfk("meeting_id", "meetings", "set null"),
        Index("ix_agent_runs_agent", "agent_id", "created_at"),
        Index("ix_agent_runs_task", "task_id", postgresql_where=text("task_id is not null")),
    )


class AgentReport(IdPk, Tenant, Stamps, Base):
    """Written output of a run. Long bodies live in Blob at {workspace_id}/runs/{run_id}/report.md."""

    __tablename__ = "agent_reports"

    run_id: Mapped[uuid.UUID | None]
    agent_id: Mapped[uuid.UUID | None]
    kind: Mapped[str]
    title: Mapped[str]
    summary: Mapped[str | None]
    body: Mapped[str | None]
    container: Mapped[str | None]
    blob_path: Mapped[str | None]
    external_url: Mapped[str | None]  # the PR or commit the agent produced; GitHub owns its state
    project_id: Mapped[uuid.UUID | None]
    work_item_id: Mapped[uuid.UUID | None]
    task_id: Mapped[uuid.UUID | None]
    sprint_id: Mapped[uuid.UUID | None]
    meeting_id: Mapped[uuid.UUID | None]
    status: Mapped[str] = mapped_column(server_default="draft")

    __table_args__ = (
        tenant_unique(),
        one_of(
            "kind",
            "plan",
            "review",
            "security",
            "architecture_debate",
            "sprint_summary",
            "standup",
            "conflict",
            "meeting_summary",
            "behavior",
            "other",
        ),
        one_of("status", "draft", "published", "archived"),
        CheckConstraint("body is not null or blob_path is not null", name="has_content"),
        tfk("run_id", "agent_runs", "set null"),
        tfk("agent_id", "members", "set null"),
        tfk("project_id", "projects", "set null"),
        tfk("work_item_id", "work_items", "set null"),
        tfk("task_id", "tasks", "set null"),
        tfk("sprint_id", "sprints", "set null"),
        tfk("meeting_id", "meetings", "set null"),
        Index(
            "ix_agent_reports_project",
            "project_id",
            "created_at",
            postgresql_where=text("project_id is not null"),
        ),
        Index("ix_agent_reports_task", "task_id", postgresql_where=text("task_id is not null")),
    )


class Approval(IdPk, Tenant, Created, Base):
    """Human sign-off on an agent's plan, pull request, or action."""

    __tablename__ = "approvals"

    run_id: Mapped[uuid.UUID | None]
    report_id: Mapped[uuid.UUID | None]
    kind: Mapped[str]
    requested_by: Mapped[uuid.UUID | None]
    assigned_to: Mapped[uuid.UUID | None]
    status: Mapped[str] = mapped_column(server_default="pending")
    decided_by: Mapped[uuid.UUID | None]
    decision_comment: Mapped[str | None]
    decided_at: Mapped[datetime | None]
    expires_at: Mapped[datetime | None]
    payload: Mapped[dict] = mapped_column(server_default=EMPTY_JSON)

    __table_args__ = (
        tenant_unique(),
        one_of("kind", "plan", "pull_request", "deploy", "action"),
        one_of("status", "pending", "approved", "rejected", "expired", "canceled"),
        tfk("run_id", "agent_runs", "set null"),
        tfk("report_id", "agent_reports", "set null"),
        tfk("requested_by", "members", "set null"),
        tfk("assigned_to", "members", "set null"),
        tfk("decided_by", "members", "set null"),
        Index("ix_approvals_pending", "assigned_to", postgresql_where=text("status = 'pending'")),
    )


class DecisionConflict(IdPk, Tenant, Created, Base):
    """Decision Ledger flagged new work that contradicts a recorded decision."""

    __tablename__ = "decision_conflicts"

    decision_id: Mapped[uuid.UUID]
    task_id: Mapped[uuid.UUID | None]
    work_item_id: Mapped[uuid.UUID | None]
    source_type: Mapped[str | None]  # message, meeting, comment ... when not a task
    source_id: Mapped[uuid.UUID | None]
    source_url: Mapped[str | None]  # e.g. the GitHub PR, which GitHub owns
    similarity: Mapped[float | None] = mapped_column(Float)
    proposal: Mapped[str | None]  # the action or change that was checked against the decision
    explanation: Mapped[str]
    status: Mapped[str] = mapped_column(server_default="open")
    flagged_by: Mapped[uuid.UUID | None]
    run_id: Mapped[uuid.UUID | None]
    resolved_by: Mapped[uuid.UUID | None]
    resolved_at: Mapped[datetime | None]
    resolution_note: Mapped[str | None]

    __table_args__ = (
        one_of("status", "open", "accepted", "dismissed", "resolved"),
        ForeignKeyConstraint(
            ["workspace_id", "decision_id"],
            ["decisions.workspace_id", "decisions.memory_id"],
            ondelete="CASCADE",
        ),
        tfk("task_id", "tasks", "set null"),
        tfk("work_item_id", "work_items", "set null"),
        tfk("flagged_by", "members", "set null"),
        tfk("run_id", "agent_runs", "set null"),
        tfk("resolved_by", "members", "set null"),
        Index(
            "ix_decision_conflicts_open",
            "workspace_id",
            "status",
            postgresql_where=text("status = 'open'"),
        ),
    )


class LlmUsage(IdPk, Tenant, Created, Base):
    """Every model call. Credits are the project's top risk, so cost is tracked per run."""

    __tablename__ = "llm_usage"

    run_id: Mapped[uuid.UUID | None]
    member_id: Mapped[uuid.UUID | None]
    task_type: Mapped[str]  # memory_steward.compress, ghost_engineer.plan, ...
    model: Mapped[str]
    input_tokens: Mapped[int] = mapped_column(server_default="0")
    output_tokens: Mapped[int] = mapped_column(server_default="0")
    cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6))
    latency_ms: Mapped[int | None]

    __table_args__ = (
        tfk("run_id", "agent_runs", "set null"),
        tfk("member_id", "members", "set null"),
        Index("ix_llm_usage_workspace_id_created_at", "workspace_id", "created_at"),
    )


class Event(Tenant, Created, Base):
    """Append-only activity and audit log: task history, recall events, approvals.

    The Month 4 evaluation (conflict frequency, review effort, coordination) reads this table.
    """

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_id: Mapped[uuid.UUID | None]
    entity_type: Mapped[str]
    entity_id: Mapped[uuid.UUID | None]
    action: Mapped[str]  # created, updated, status_changed, recalled, approved, ...
    changes: Mapped[dict] = mapped_column(server_default=EMPTY_JSON)
    run_id: Mapped[uuid.UUID | None]
    request_id: Mapped[str | None]

    __table_args__ = (
        tfk("actor_id", "members", "set null"),
        Index("ix_events_entity", "workspace_id", "entity_type", "entity_id", "created_at"),
        Index("ix_events_workspace_id_created_at", "workspace_id", "created_at"),
    )


class Attachment(IdPk, Tenant, Created, Base):
    """Attaches a file to exactly one thing: a chat message, comment, task, doc, report, ..."""

    __tablename__ = "attachments"

    file_id: Mapped[uuid.UUID]
    message_id: Mapped[uuid.UUID | None]
    comment_id: Mapped[uuid.UUID | None]
    task_id: Mapped[uuid.UUID | None]
    work_item_id: Mapped[uuid.UUID | None]
    project_id: Mapped[uuid.UUID | None]
    project_update_id: Mapped[uuid.UUID | None]
    doc_id: Mapped[uuid.UUID | None]
    agent_report_id: Mapped[uuid.UUID | None]
    meeting_id: Mapped[uuid.UUID | None]
    sort_order: Mapped[float] = mapped_column(Float, server_default="0")
    created_by: Mapped[uuid.UUID | None]

    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(message_id, comment_id, task_id, work_item_id, project_id,"
            " project_update_id, doc_id, agent_report_id, meeting_id) = 1",
            name="single_target",
        ),
        tfk("file_id", "files", "cascade"),
        tfk("message_id", "messages", "cascade"),
        tfk("comment_id", "comments", "cascade"),
        tfk("task_id", "tasks", "cascade"),
        tfk("work_item_id", "work_items", "cascade"),
        tfk("project_id", "projects", "cascade"),
        tfk("project_update_id", "project_updates", "cascade"),
        tfk("doc_id", "docs", "cascade"),
        tfk("agent_report_id", "agent_reports", "cascade"),
        tfk("meeting_id", "meetings", "cascade"),
        tfk("created_by", "members", "set null"),
        Index("ix_attachments_file_id", "file_id"),
        Index(
            "ix_attachments_message", "message_id", postgresql_where=text("message_id is not null")
        ),
        Index("ix_attachments_task", "task_id", postgresql_where=text("task_id is not null")),
        Index("ix_attachments_doc", "doc_id", postgresql_where=text("doc_id is not null")),
        Index(
            "ix_attachments_comment", "comment_id", postgresql_where=text("comment_id is not null")
        ),
    )
