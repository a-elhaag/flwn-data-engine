import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, IdMixin, TimestampMixin
from db.enums import ProjectStatus


class Project(IdMixin, TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("id", "organization_id"),
        UniqueConstraint("organization_id", "key"),
        ForeignKeyConstraint(
            ["workspace_id", "organization_id"],
            ["workspaces.id", "workspaces.organization_id"],
        ),
        ForeignKeyConstraint(
            ["created_by_id", "organization_id"],
            ["members.id", "members.organization_id"],
        ),
        CheckConstraint("key ~ '^[A-Z]{2,10}$'", name="key_format"),
        CheckConstraint("task_counter >= 0", name="task_counter_nonneg"),
        CheckConstraint("char_length(btrim(name)) > 0", name="name_not_blank"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    created_by_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    key: Mapped[str] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(5000))
    status: Mapped[ProjectStatus] = mapped_column(
        Enum(ProjectStatus, name="project_status"), server_default="ACTIVE"
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    task_counter: Mapped[int] = mapped_column(Integer, server_default="0")


Index(
    "uq_projects_workspace_id_name_active",
    Project.workspace_id,
    func.lower(Project.name),
    unique=True,
    postgresql_where=text("archived_at IS NULL"),
)


class ProjectTeam(Base):
    __tablename__ = "project_teams"
    __table_args__ = (
        PrimaryKeyConstraint("project_id", "team_id"),
        ForeignKeyConstraint(
            ["project_id", "organization_id"],
            ["projects.id", "projects.organization_id"],
        ),
        ForeignKeyConstraint(
            ["team_id", "organization_id"], ["teams.id", "teams.organization_id"]
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
