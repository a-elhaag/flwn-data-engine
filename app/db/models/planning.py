"""Linear-style planning: projects, backlogs, work items, sprints, tasks, labels, views."""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    Index,
    SmallInteger,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    EMPTY_JSON,
    EMPTY_LIST,
    PRIORITIES,
    WORK_STATUSES,
    ZERO_UUID,
    Base,
    Created,
    IdPk,
    SoftDelete,
    Stamps,
    Tenant,
    one_of,
    pfk,
    project_unique,
    tenant_unique,
    tfk,
)


class Project(IdPk, Tenant, Stamps, SoftDelete, Base):
    __tablename__ = "projects"

    team_id: Mapped[uuid.UUID | None]  # null = workspace-wide project
    key: Mapped[str]  # task identifiers look like FLWN-12
    name: Mapped[str]
    description: Mapped[str | None]
    status: Mapped[str] = mapped_column(server_default="planned")
    lead_id: Mapped[uuid.UUID | None]
    start_date: Mapped[date | None]
    target_date: Mapped[date | None]
    icon: Mapped[str | None]
    color: Mapped[str | None]
    task_counter: Mapped[int] = mapped_column(server_default="0")
    created_by: Mapped[uuid.UUID | None]

    __table_args__ = (
        tenant_unique(),
        one_of("status", "backlog", "planned", "in_progress", "paused", "completed", "canceled"),
        tfk("team_id", "teams", "set null"),
        tfk("lead_id", "members", "set null"),
        tfk("created_by", "members", "set null"),
        Index("ix_projects_workspace_id_team_id", "workspace_id", "team_id"),
    )


Index("uq_projects_workspace_id_key", Project.workspace_id, func.lower(Project.key), unique=True)


class ProjectUpdate(IdPk, Tenant, Stamps, Base):
    """Periodic project status post (health plus notes)."""

    __tablename__ = "project_updates"

    project_id: Mapped[uuid.UUID]
    author_id: Mapped[uuid.UUID | None]
    health: Mapped[str]
    body: Mapped[str]

    __table_args__ = (
        tenant_unique(),
        one_of("health", "on_track", "at_risk", "off_track"),
        tfk("project_id", "projects", "cascade"),
        tfk("author_id", "members", "set null"),
        Index("ix_project_updates_project_id_created_at", "project_id", "created_at"),
    )


class Milestone(IdPk, Tenant, Stamps, Base):
    __tablename__ = "milestones"

    project_id: Mapped[uuid.UUID]
    name: Mapped[str]
    description: Mapped[str | None]
    target_date: Mapped[date | None]
    status: Mapped[str] = mapped_column(server_default="planned")
    sort_order: Mapped[float] = mapped_column(Float, server_default="0")

    __table_args__ = (
        tenant_unique(),
        project_unique(),
        one_of("status", "planned", "in_progress", "done"),
        tfk("project_id", "projects", "cascade"),
    )


class Backlog(IdPk, Tenant, Stamps, Base):
    __tablename__ = "backlogs"

    project_id: Mapped[uuid.UUID]
    name: Mapped[str]
    description: Mapped[str | None]

    __table_args__ = (
        tenant_unique(),
        project_unique(),
        tfk("project_id", "projects", "cascade"),
    )


class Sprint(IdPk, Tenant, Stamps, Base):
    __tablename__ = "sprints"

    project_id: Mapped[uuid.UUID]
    name: Mapped[str]
    goal: Mapped[str | None]
    status: Mapped[str] = mapped_column(server_default="planned")
    start_date: Mapped[date | None]
    end_date: Mapped[date | None]

    __table_args__ = (
        tenant_unique(),
        project_unique(),
        one_of("status", "planned", "active", "completed"),
        CheckConstraint(
            "end_date is null or start_date is null or end_date >= start_date", name="dates"
        ),
        tfk("project_id", "projects", "cascade"),
        Index("ix_sprints_project_id_status", "project_id", "status"),
    )


class WorkItem(IdPk, Tenant, Stamps, SoftDelete, Base):
    __tablename__ = "work_items"

    project_id: Mapped[uuid.UUID]  # denormalized; the FK to backlogs keeps it consistent
    backlog_id: Mapped[uuid.UUID]
    title: Mapped[str]
    description: Mapped[str | None]
    type: Mapped[str]
    priority: Mapped[str] = mapped_column(server_default="none")
    status: Mapped[str] = mapped_column(server_default="backlog")
    created_by: Mapped[uuid.UUID | None]

    __table_args__ = (
        tenant_unique(),
        project_unique(),
        one_of("type", "FEATURE", "STORY", "BUG"),
        one_of("priority", *PRIORITIES),
        one_of("status", *WORK_STATUSES),
        pfk("backlog_id", "backlogs", "cascade"),
        tfk("created_by", "members", "set null"),
        Index("ix_work_items_backlog_id_status", "backlog_id", "status"),
    )


class Task(IdPk, Tenant, Stamps, SoftDelete, Base):
    __tablename__ = "tasks"

    project_id: Mapped[uuid.UUID]
    work_item_id: Mapped[uuid.UUID]
    sprint_id: Mapped[uuid.UUID | None]
    milestone_id: Mapped[uuid.UUID | None]
    parent_task_id: Mapped[uuid.UUID | None]  # sub-tasks
    number: Mapped[int | None]  # per project, assigned by trigger: FLWN-12
    title: Mapped[str]
    description: Mapped[str | None]
    requirements: Mapped[str | None]
    acceptance_criteria: Mapped[str | None]
    priority: Mapped[str] = mapped_column(server_default="none")
    status: Mapped[str] = mapped_column(server_default="backlog")
    estimate: Mapped[int | None] = mapped_column(SmallInteger)
    due_date: Mapped[date | None]
    started_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]
    sort_order: Mapped[float] = mapped_column(Float, server_default="0")
    assignee_id: Mapped[uuid.UUID | None]
    created_by: Mapped[uuid.UUID | None]

    __table_args__ = (
        tenant_unique(),
        project_unique(),
        one_of("priority", *PRIORITIES),
        one_of("status", *WORK_STATUSES),
        tfk("project_id", "projects", "cascade"),
        pfk("work_item_id", "work_items", "cascade"),
        pfk("sprint_id", "sprints", "set null"),
        pfk("milestone_id", "milestones", "set null"),
        tfk("parent_task_id", "tasks", "set null"),
        tfk("assignee_id", "members", "set null"),
        tfk("created_by", "members", "set null"),
        Index("uq_tasks_project_id_number", "project_id", "number", unique=True),
        Index("ix_tasks_project_id_status", "project_id", "status"),
        Index(
            "ix_tasks_assignee",
            "workspace_id",
            "assignee_id",
            postgresql_where=text("assignee_id is not null"),
        ),
        Index("ix_tasks_sprint_id", "sprint_id", postgresql_where=text("sprint_id is not null")),
        Index("ix_tasks_work_item_id", "work_item_id"),
        Index(
            "ix_tasks_parent_task_id",
            "parent_task_id",
            postgresql_where=text("parent_task_id is not null"),
        ),
    )


class TaskRelation(Tenant, Created, Base):
    __tablename__ = "task_relations"

    task_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    related_task_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(primary_key=True)
    created_by: Mapped[uuid.UUID | None]

    __table_args__ = (
        one_of("type", "blocks", "relates", "duplicates"),
        CheckConstraint("task_id <> related_task_id", name="distinct_tasks"),
        tfk("task_id", "tasks", "cascade"),
        tfk("related_task_id", "tasks", "cascade"),
        tfk("created_by", "members", "set null"),
        Index("ix_task_relations_related_task_id", "related_task_id"),
    )


class Label(IdPk, Tenant, Created, Base):
    __tablename__ = "labels"

    team_id: Mapped[uuid.UUID | None]  # null = workspace-wide label
    name: Mapped[str]
    color: Mapped[str | None]
    description: Mapped[str | None]

    __table_args__ = (tenant_unique(), tfk("team_id", "teams", "cascade"))


Index(
    "uq_labels_name",
    Label.workspace_id,
    func.coalesce(Label.team_id, text(f"'{ZERO_UUID}'::uuid")),
    func.lower(Label.name),
    unique=True,
)


class TaskLabel(Tenant, Base):
    __tablename__ = "task_labels"

    task_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    label_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)

    __table_args__ = (
        tfk("task_id", "tasks", "cascade"),
        tfk("label_id", "labels", "cascade"),
        Index("ix_task_labels_label_id", "label_id"),
    )


class WorkItemLabel(Tenant, Base):
    __tablename__ = "work_item_labels"

    work_item_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    label_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)

    __table_args__ = (
        tfk("work_item_id", "work_items", "cascade"),
        tfk("label_id", "labels", "cascade"),
        Index("ix_work_item_labels_label_id", "label_id"),
    )


class SavedView(IdPk, Tenant, Stamps, Base):
    """Saved filter and layout: Linear views, Notion database views."""

    __tablename__ = "saved_views"

    owner_id: Mapped[uuid.UUID | None]
    team_id: Mapped[uuid.UUID | None]
    project_id: Mapped[uuid.UUID | None]
    name: Mapped[str]
    entity_type: Mapped[str]
    layout: Mapped[str] = mapped_column(server_default="list")
    filter: Mapped[dict] = mapped_column(server_default=EMPTY_JSON)
    sort: Mapped[list] = mapped_column(server_default=EMPTY_LIST)
    is_shared: Mapped[bool] = mapped_column(Boolean, server_default="false")

    __table_args__ = (
        tenant_unique(),
        one_of("entity_type", "task", "work_item", "project", "doc", "collection"),
        one_of("layout", "list", "board", "table", "calendar", "timeline", "gallery"),
        tfk("owner_id", "members", "cascade"),
        tfk("team_id", "teams", "cascade"),
        tfk("project_id", "projects", "cascade"),
    )
