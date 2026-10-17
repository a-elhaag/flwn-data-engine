import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, IdMixin, TimestampMixin
from db.enums import TeamMemberRole


class Team(IdMixin, TimestampMixin, Base):
    __tablename__ = "teams"
    __table_args__ = (
        UniqueConstraint("id", "organization_id"),
        ForeignKeyConstraint(
            ["workspace_id", "organization_id"],
            ["workspaces.id", "workspaces.organization_id"],
        ),
        ForeignKeyConstraint(
            ["created_by_id", "organization_id"],
            ["members.id", "members.organization_id"],
        ),
        CheckConstraint("char_length(btrim(name)) > 0", name="name_not_blank"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    created_by_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(5000))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index(
    "uq_teams_workspace_id_name_active",
    Team.workspace_id,
    func.lower(Team.name),
    unique=True,
    postgresql_where=text("archived_at IS NULL"),
)


class TeamMember(Base):
    __tablename__ = "team_members"
    __table_args__ = (
        PrimaryKeyConstraint("team_id", "member_id"),
        ForeignKeyConstraint(
            ["team_id", "organization_id"], ["teams.id", "teams.organization_id"]
        ),
        ForeignKeyConstraint(
            ["member_id", "organization_id"],
            ["members.id", "members.organization_id"],
        ),
    )

    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    role: Mapped[TeamMemberRole] = mapped_column(
        Enum(TeamMemberRole, name="team_member_role"), server_default="MEMBER"
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
