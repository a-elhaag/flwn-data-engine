"""Notion-style docs and databases, chat, comments, reactions, links, notifications."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, Float, Index, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    EMPTY_JSON,
    EMPTY_LIST,
    Base,
    Created,
    IdPk,
    SoftDelete,
    Stamps,
    Tenant,
    one_of,
    tenant_unique,
    tfk,
)

# ----------------------------------------------------------- docs (Notion)


class Collection(IdPk, Tenant, Stamps, SoftDelete, Base):
    """A Notion database: a schema of properties. Each row is a doc."""

    __tablename__ = "collections"

    team_id: Mapped[uuid.UUID | None]
    project_id: Mapped[uuid.UUID | None]
    name: Mapped[str]
    description: Mapped[str | None]
    icon: Mapped[str | None]
    # e.g. [{"key": "owner", "type": "member"}, {"key": "due", "type": "date"}]
    schema: Mapped[list] = mapped_column(server_default=EMPTY_LIST)
    created_by: Mapped[uuid.UUID | None]

    __table_args__ = (
        tenant_unique(),
        tfk("team_id", "teams", "set null"),
        tfk("project_id", "projects", "cascade"),
        tfk("created_by", "members", "set null"),
    )


class Doc(IdPk, Tenant, Stamps, SoftDelete, Base):
    __tablename__ = "docs"

    team_id: Mapped[uuid.UUID | None]
    project_id: Mapped[uuid.UUID | None]  # "the docs for this project"
    parent_doc_id: Mapped[uuid.UUID | None]  # page tree
    collection_id: Mapped[uuid.UUID | None]  # set when the doc is a database row
    title: Mapped[str] = mapped_column(server_default="Untitled")
    icon: Mapped[str | None]
    cover_file_id: Mapped[uuid.UUID | None]
    properties: Mapped[dict] = mapped_column(server_default=EMPTY_JSON)  # values for the schema
    sort_order: Mapped[float] = mapped_column(Float, server_default="0")
    is_template: Mapped[bool] = mapped_column(Boolean, server_default="false")
    is_locked: Mapped[bool] = mapped_column(Boolean, server_default="false")
    published_slug: Mapped[str | None]  # set = public page, unique case-insensitively
    created_by: Mapped[uuid.UUID | None]
    last_edited_by: Mapped[uuid.UUID | None]
    archived_at: Mapped[datetime | None]

    __table_args__ = (
        tenant_unique(),
        tfk("team_id", "teams", "set null"),
        tfk("project_id", "projects", "cascade"),
        tfk("parent_doc_id", "docs", "cascade"),
        tfk("collection_id", "collections", "cascade"),
        tfk("cover_file_id", "files", "set null"),
        tfk("created_by", "members", "set null"),
        tfk("last_edited_by", "members", "set null"),
        Index(
            "ix_docs_project",
            "workspace_id",
            "project_id",
            postgresql_where=text("project_id is not null"),
        ),
        Index(
            "ix_docs_parent_doc_id",
            "parent_doc_id",
            postgresql_where=text("parent_doc_id is not null"),
        ),
        Index(
            "ix_docs_collection_id",
            "collection_id",
            postgresql_where=text("collection_id is not null"),
        ),
    )


Index(
    "uq_docs_published_slug",
    func.lower(Doc.published_slug),
    unique=True,
    postgresql_where=text("published_slug is not null"),
)

BLOCK_TYPES = (
    "paragraph",
    "heading_1",
    "heading_2",
    "heading_3",
    "bulleted_list",
    "numbered_list",
    "todo",
    "toggle",
    "quote",
    "callout",
    "code",
    "divider",
    "image",
    "file",
    "embed",
    "table",
    "table_row",
    "task_embed",
    "doc_link",
    "columns",
    "column",
)


class DocBlock(IdPk, Tenant, Stamps, Base):
    """Blocks form a tree per doc; content holds rich text and attributes as JSON.

    ponytail: last-write-wins per block. Real-time co-editing (CRDT) is an upgrade for the Go hub.
    """

    __tablename__ = "doc_blocks"

    doc_id: Mapped[uuid.UUID]
    parent_block_id: Mapped[uuid.UUID | None]
    type: Mapped[str]
    content: Mapped[dict] = mapped_column(server_default=EMPTY_JSON)
    sort_order: Mapped[float] = mapped_column(Float, server_default="0")
    created_by: Mapped[uuid.UUID | None]
    last_edited_by: Mapped[uuid.UUID | None]

    __table_args__ = (
        tenant_unique(),
        one_of("type", *BLOCK_TYPES),
        tfk("doc_id", "docs", "cascade"),
        tfk("parent_block_id", "doc_blocks", "cascade"),
        tfk("created_by", "members", "set null"),
        tfk("last_edited_by", "members", "set null"),
        Index(
            "ix_doc_blocks_doc_id_parent_block_id_sort_order",
            "doc_id",
            "parent_block_id",
            "sort_order",
        ),
    )


class DocVersion(IdPk, Tenant, Created, Base):
    __tablename__ = "doc_versions"

    doc_id: Mapped[uuid.UUID]
    version: Mapped[int]
    title: Mapped[str]
    snapshot: Mapped[dict]  # the full block tree at that point
    created_by: Mapped[uuid.UUID | None]

    __table_args__ = (
        UniqueConstraint("doc_id", "version"),
        tfk("doc_id", "docs", "cascade"),
        tfk("created_by", "members", "set null"),
    )


# ------------------------------------------------------------------ chat


class Channel(IdPk, Tenant, Stamps, Base):
    __tablename__ = "channels"

    team_id: Mapped[uuid.UUID | None]
    project_id: Mapped[uuid.UUID | None]
    type: Mapped[str]
    name: Mapped[str | None]
    topic: Mapped[str | None]
    is_private: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_by: Mapped[uuid.UUID | None]
    archived_at: Mapped[datetime | None]

    __table_args__ = (
        tenant_unique(),
        one_of("type", "team", "project", "group", "dm", "agent"),
        tfk("team_id", "teams", "cascade"),
        tfk("project_id", "projects", "cascade"),
        tfk("created_by", "members", "set null"),
    )


class ChannelMember(Tenant, Base):
    __tablename__ = "channel_members"

    channel_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    member_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    role: Mapped[str] = mapped_column(server_default="member")
    last_read_at: Mapped[datetime | None]
    muted: Mapped[bool] = mapped_column(Boolean, server_default="false")
    joined_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    __table_args__ = (
        one_of("role", "owner", "member"),
        tfk("channel_id", "channels", "cascade"),
        tfk("member_id", "members", "cascade"),
        Index("ix_channel_members_member", "workspace_id", "member_id"),
    )


class Message(IdPk, Tenant, Created, Base):
    __tablename__ = "messages"

    channel_id: Mapped[uuid.UUID]
    author_id: Mapped[uuid.UUID | None]
    parent_message_id: Mapped[uuid.UUID | None]  # thread reply
    kind: Mapped[str] = mapped_column(server_default="text")
    body: Mapped[str | None]  # markdown; a voice note's body is its transcript
    body_json: Mapped[dict | None]
    metadata_: Mapped[dict] = mapped_column("metadata", server_default=EMPTY_JSON)
    pinned_at: Mapped[datetime | None]
    edited_at: Mapped[datetime | None]
    deleted_at: Mapped[datetime | None]

    __table_args__ = (
        tenant_unique(),
        one_of("kind", "text", "voice_note", "image", "file", "system", "agent_report"),
        tfk("channel_id", "channels", "cascade"),
        tfk("author_id", "members", "set null"),
        tfk("parent_message_id", "messages", "set null"),
        Index("ix_messages_channel_id_created_at", "channel_id", "created_at"),
        Index(
            "ix_messages_parent_message_id",
            "parent_message_id",
            postgresql_where=text("parent_message_id is not null"),
        ),
    )


# --------------------------------------------------------------- comments


class Comment(IdPk, Tenant, Stamps, Base):
    """Exactly one target. Real foreign keys beat a (type, id) pair: the database stops comments
    pointing at deleted or foreign-workspace rows."""

    __tablename__ = "comments"

    task_id: Mapped[uuid.UUID | None]
    work_item_id: Mapped[uuid.UUID | None]
    project_id: Mapped[uuid.UUID | None]
    project_update_id: Mapped[uuid.UUID | None]
    sprint_id: Mapped[uuid.UUID | None]
    doc_id: Mapped[uuid.UUID | None]
    block_id: Mapped[uuid.UUID | None]  # optional: a specific block of doc_id
    parent_comment_id: Mapped[uuid.UUID | None]  # reply
    author_id: Mapped[uuid.UUID | None]
    body: Mapped[str]
    body_json: Mapped[dict | None]
    resolved_at: Mapped[datetime | None]
    resolved_by: Mapped[uuid.UUID | None]
    edited_at: Mapped[datetime | None]
    deleted_at: Mapped[datetime | None]

    __table_args__ = (
        tenant_unique(),
        CheckConstraint(
            "num_nonnulls(task_id, work_item_id, project_id, project_update_id, sprint_id, doc_id) = 1",
            name="single_target",
        ),
        CheckConstraint("block_id is null or doc_id is not null", name="block_needs_doc"),
        tfk("task_id", "tasks", "cascade"),
        tfk("work_item_id", "work_items", "cascade"),
        tfk("project_id", "projects", "cascade"),
        tfk("project_update_id", "project_updates", "cascade"),
        tfk("sprint_id", "sprints", "cascade"),
        tfk("doc_id", "docs", "cascade"),
        tfk("block_id", "doc_blocks", "set null"),
        tfk("parent_comment_id", "comments", "cascade"),
        tfk("author_id", "members", "set null"),
        tfk("resolved_by", "members", "set null"),
        Index(
            "ix_comments_task",
            "task_id",
            "created_at",
            postgresql_where=text("task_id is not null"),
        ),
        Index(
            "ix_comments_work_item",
            "work_item_id",
            "created_at",
            postgresql_where=text("work_item_id is not null"),
        ),
        Index(
            "ix_comments_project",
            "project_id",
            "created_at",
            postgresql_where=text("project_id is not null"),
        ),
        Index(
            "ix_comments_doc", "doc_id", "created_at", postgresql_where=text("doc_id is not null")
        ),
        Index(
            "ix_comments_project_update",
            "project_update_id",
            postgresql_where=text("project_update_id is not null"),
        ),
        Index("ix_comments_sprint", "sprint_id", postgresql_where=text("sprint_id is not null")),
        Index(
            "ix_comments_parent",
            "parent_comment_id",
            postgresql_where=text("parent_comment_id is not null"),
        ),
    )


class Reaction(IdPk, Tenant, Created, Base):
    __tablename__ = "reactions"

    member_id: Mapped[uuid.UUID]
    emoji: Mapped[str]
    comment_id: Mapped[uuid.UUID | None]
    message_id: Mapped[uuid.UUID | None]
    task_id: Mapped[uuid.UUID | None]

    __table_args__ = (
        CheckConstraint("num_nonnulls(comment_id, message_id, task_id) = 1", name="single_target"),
        UniqueConstraint(
            "member_id",
            "emoji",
            "comment_id",
            "message_id",
            "task_id",
            postgresql_nulls_not_distinct=True,
        ),
        tfk("member_id", "members", "cascade"),
        tfk("comment_id", "comments", "cascade"),
        tfk("message_id", "messages", "cascade"),
        tfk("task_id", "tasks", "cascade"),
    )


# ------------------------------------------------- links, mentions, social


class Link(IdPk, Tenant, Created, Base):
    """Generic edges: @mentions, task-to-doc links, backlinks.

    Polymorphic on purpose: soft references the app cleans up when a source or target goes away.
    """

    __tablename__ = "links"

    kind: Mapped[str]
    source_type: Mapped[str]
    source_id: Mapped[uuid.UUID]
    target_type: Mapped[str]
    target_id: Mapped[uuid.UUID]

    __table_args__ = (
        one_of("kind", "mention", "link"),
        one_of(
            "source_type",
            "comment",
            "message",
            "doc",
            "doc_block",
            "task",
            "work_item",
            "project",
            "agent_report",
        ),
        one_of(
            "target_type",
            "member",
            "task",
            "work_item",
            "project",
            "sprint",
            "doc",
            "file",
            "meeting",
            "decision",
            "channel",
        ),
        UniqueConstraint("source_type", "source_id", "target_type", "target_id", "kind"),
        Index("ix_links_target", "workspace_id", "target_type", "target_id"),
    )


class Subscription(Tenant, Created, Base):
    __tablename__ = "subscriptions"

    member_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(primary_key=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)

    __table_args__ = (
        one_of("entity_type", "task", "work_item", "project", "doc", "channel", "sprint"),
        tfk("member_id", "members", "cascade"),
        Index("ix_subscriptions_entity", "workspace_id", "entity_type", "entity_id"),
    )


class Favorite(IdPk, Tenant, Created, Base):
    __tablename__ = "favorites"

    member_id: Mapped[uuid.UUID]
    entity_type: Mapped[str]
    entity_id: Mapped[uuid.UUID]
    sort_order: Mapped[float] = mapped_column(Float, server_default="0")

    __table_args__ = (
        one_of("entity_type", "task", "project", "doc", "channel", "saved_view", "collection"),
        UniqueConstraint("member_id", "entity_type", "entity_id"),
        tfk("member_id", "members", "cascade"),
    )


class Notification(IdPk, Tenant, Created, Base):
    __tablename__ = "notifications"

    recipient_id: Mapped[uuid.UUID]
    actor_id: Mapped[uuid.UUID | None]
    type: Mapped[str]
    entity_type: Mapped[str | None]
    entity_id: Mapped[uuid.UUID | None]
    title: Mapped[str]
    body: Mapped[str | None]
    read_at: Mapped[datetime | None]
    archived_at: Mapped[datetime | None]
    snoozed_until: Mapped[datetime | None]

    __table_args__ = (
        one_of(
            "type",
            "assigned",
            "mentioned",
            "commented",
            "replied",
            "status_changed",
            "due_soon",
            "approval_requested",
            "approval_decided",
            "conflict_flagged",
            "agent_report",
            "invited",
            "meeting_started",
            "meeting_summary",
        ),
        tfk("recipient_id", "members", "cascade"),
        tfk("actor_id", "members", "set null"),
        Index(
            "ix_notifications_inbox",
            "recipient_id",
            "created_at",
            postgresql_where=text("archived_at is null"),
        ),
        Index(
            "ix_notifications_unread",
            "recipient_id",
            postgresql_where=text("read_at is null and archived_at is null"),
        ),
    )
