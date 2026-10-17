import uuid
from datetime import date

from sqlalchemy import (
    CheckConstraint,
    Date,
    Enum,
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
from db.enums import SprintStatus


class Sprint(IdMixin, TimestampMixin, Base):
    __tablename__ = "sprints"
    __table_args__ = (
        UniqueConstraint("id", "project_id"),
        ForeignKeyConstraint(
            ["project_id", "team_id"],
            ["project_teams.project_id", "project_teams.team_id"],
        ),
        ForeignKeyConstraint(
            ["project_id", "organization_id"],
            ["projects.id", "projects.organization_id"],
        ),
        ForeignKeyConstraint(
            ["created_by_id", "organization_id"],
            ["members.id", "members.organization_id"],
        ),
        CheckConstraint(
            "end_date IS NULL OR start_date IS NULL OR end_date >= start_date",
            name="dates",
        ),
        CheckConstraint("char_length(btrim(name)) > 0", name="name_not_blank"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    created_by_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    name: Mapped[str] = mapped_column(String(100))
    goal: Mapped[str | None] = mapped_column(String(5000))
    status: Mapped[SprintStatus] = mapped_column(
        Enum(SprintStatus, name="sprint_status"), server_default="PLANNED"
    )
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)


Index(
    "uq_sprints_project_id_team_id_name",
    Sprint.project_id,
    Sprint.team_id,
    func.lower(Sprint.name),
    unique=True,
)
Index(
    "uq_sprints_project_id_team_id_active",
    Sprint.project_id,
    Sprint.team_id,
    unique=True,
    postgresql_where=text("status = 'ACTIVE'"),
)
