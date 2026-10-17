import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, IdMixin, TimestampMixin
from db.enums import Priority, TaskStatus


class Task(IdMixin, TimestampMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("project_id", "number"),
        ForeignKeyConstraint(
            ["project_id", "organization_id"],
            ["projects.id", "projects.organization_id"],
        ),
        ForeignKeyConstraint(
            ["work_item_id", "project_id"],
            ["work_items.id", "work_items.project_id"],
            ondelete="SET NULL (work_item_id)",
        ),
        ForeignKeyConstraint(
            ["sprint_id", "project_id"],
            ["sprints.id", "sprints.project_id"],
            ondelete="SET NULL (sprint_id)",
        ),
        ForeignKeyConstraint(
            ["assignee_id", "organization_id"],
            ["members.id", "members.organization_id"],
        ),
        ForeignKeyConstraint(
            ["created_by_id", "organization_id"],
            ["members.id", "members.organization_id"],
        ),
        CheckConstraint("number >= 1", name="number_positive"),
        CheckConstraint(
            "(status = 'DONE') = (completed_at IS NOT NULL)", name="done_completed"
        ),
        CheckConstraint("char_length(btrim(title)) > 0", name="title_not_blank"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    number: Mapped[int] = mapped_column(Integer)
    work_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    sprint_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(5000))
    requirements: Mapped[str | None] = mapped_column(String(10000))
    acceptance_criteria: Mapped[str | None] = mapped_column(String(10000))
    priority: Mapped[Priority] = mapped_column(
        Enum(Priority, name="priority"), server_default="MEDIUM"
    )
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, name="task_status"), server_default="TODO"
    )
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
