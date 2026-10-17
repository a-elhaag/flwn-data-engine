import uuid

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, IdMixin, TimestampMixin
from db.enums import Priority, WorkItemStatus, WorkItemType


class WorkItem(IdMixin, TimestampMixin, Base):
    __tablename__ = "work_items"
    __table_args__ = (
        UniqueConstraint("id", "project_id"),
        ForeignKeyConstraint(
            ["project_id", "organization_id"],
            ["projects.id", "projects.organization_id"],
        ),
        ForeignKeyConstraint(
            ["created_by_id", "organization_id"],
            ["members.id", "members.organization_id"],
        ),
        CheckConstraint("char_length(btrim(title)) > 0", name="title_not_blank"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    created_by_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(5000))
    type: Mapped[WorkItemType] = mapped_column(Enum(WorkItemType, name="work_item_type"))
    priority: Mapped[Priority] = mapped_column(
        Enum(Priority, name="priority"), server_default="MEDIUM"
    )
    status: Mapped[WorkItemStatus] = mapped_column(
        Enum(WorkItemStatus, name="work_item_status"), server_default="OPEN"
    )
