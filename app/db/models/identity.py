"""Users, workspaces, members (a user's membership in one workspace), teams."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    EMPTY_JSON,
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

AGENT_KINDS = (
    "atom",
    "team_agent",
    "ghost_engineer",
    "decision_ledger",
    "memory_steward",
    "scrum_master",
    "security",
    "architecture",
    "behavioral",
)


class User(IdPk, Stamps, SoftDelete, Base):
    """Global identity of a human. One user can be a member of many workspaces."""

    __tablename__ = "users"

    email: Mapped[str]  # unique case-insensitively, see the index below
    name: Mapped[str]
    avatar_url: Mapped[str | None]
    settings: Mapped[dict] = mapped_column(server_default=EMPTY_JSON)


Index("uq_users_email", func.lower(User.email), unique=True)


class Workspace(IdPk, Stamps, SoftDelete, Base):
    __tablename__ = "workspaces"

    name: Mapped[str]
    slug: Mapped[str]  # unique case-insensitively, see the index below
    description: Mapped[str | None]
    # retention, defaults, feature flags: e.g. {"recording_retention_days": 90}
    settings: Mapped[dict] = mapped_column(server_default=EMPTY_JSON)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


Index("uq_workspaces_slug", func.lower(Workspace.slug), unique=True)


class Member(IdPk, Tenant, Stamps, SoftDelete, Base):
    """A human (user_id set) or an AI agent (agent_kind set) inside ONE workspace."""

    __tablename__ = "members"

    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    type: Mapped[str]
    name: Mapped[str | None]  # AI members only; a human's name is users.name
    role: Mapped[str] = mapped_column(server_default="member")
    status: Mapped[str] = mapped_column(server_default="active")
    agent_kind: Mapped[str | None]
    avatar_url: Mapped[str | None]  # AI members only; a human's avatar is users.avatar_url
    settings: Mapped[dict] = mapped_column(server_default=EMPTY_JSON)

    __table_args__ = (
        tenant_unique(),
        UniqueConstraint("workspace_id", "user_id"),
        one_of("type", "HUMAN", "AI"),
        one_of("role", "owner", "admin", "member", "viewer"),
        one_of(
            "status", "active", "suspended"
        ),  # a pending invite is a row in invitations, not a member
        CheckConstraint(
            "agent_kind is null or agent_kind in ("
            + ", ".join(f"'{kind}'" for kind in AGENT_KINDS)
            + ")",
            name="agent_kind",
        ),
        CheckConstraint(
            "(type = 'HUMAN' and user_id is not null and agent_kind is null and name is null and avatar_url is null)"
            " or (type = 'AI' and user_id is null and agent_kind is not null and name is not null)",
            name="human_or_agent",
        ),
    )


class Invitation(IdPk, Tenant, Created, Base):
    __tablename__ = "invitations"

    email: Mapped[str]
    role: Mapped[str] = mapped_column(server_default="member")
    token_hash: Mapped[str] = mapped_column(unique=True)
    invited_by: Mapped[uuid.UUID | None]
    status: Mapped[str] = mapped_column(server_default="pending")
    expires_at: Mapped[datetime]
    accepted_at: Mapped[datetime | None]

    __table_args__ = (
        one_of("role", "admin", "member", "viewer"),
        one_of("status", "pending", "accepted", "revoked", "expired"),
        tfk("invited_by", "members", "set null"),
    )


class Team(IdPk, Tenant, Stamps, SoftDelete, Base):
    __tablename__ = "teams"

    key: Mapped[str]  # short prefix such as ENG, unique per workspace case-insensitively
    name: Mapped[str]
    description: Mapped[str | None]
    icon: Mapped[str | None]
    color: Mapped[str | None]
    # stored now, enforced later: private teams hide their projects, tasks and memories
    visibility: Mapped[str] = mapped_column(server_default="workspace")

    __table_args__ = (
        tenant_unique(),
        one_of("visibility", "workspace", "private"),
    )


Index("uq_teams_workspace_id_key", Team.workspace_id, func.lower(Team.key), unique=True)


class TeamMember(Tenant, Base):
    __tablename__ = "team_members"

    team_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    member_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    role: Mapped[str] = mapped_column(server_default="member")
    joined_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    __table_args__ = (
        one_of("role", "lead", "member"),
        tfk("team_id", "teams", "cascade"),
        tfk("member_id", "members", "cascade"),
    )
