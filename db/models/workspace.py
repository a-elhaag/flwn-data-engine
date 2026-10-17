import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, IdMixin, TimestampMixin


class Workspace(IdMixin, TimestampMixin, Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        UniqueConstraint("id", "organization_id"),
        ForeignKeyConstraint(
            ["created_by_id", "organization_id"],
            ["members.id", "members.organization_id"],
        ),
        CheckConstraint("char_length(btrim(name)) > 0", name="name_not_blank"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(5000))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index(
    "uq_workspaces_organization_id_name_active",
    Workspace.organization_id,
    func.lower(Workspace.name),
    unique=True,
    postgresql_where=text("archived_at IS NULL"),
)
