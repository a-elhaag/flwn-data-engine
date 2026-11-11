"""Token deny list and the auth store: password hashes, refresh sessions, login codes and OAuth
links. The backend owns the logic; these tables keep only hashes and never the raw secrets."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Identity, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Created, one_of


class RevokedToken(Base):
    __tablename__ = "revoked_tokens"

    jti: Mapped[str] = mapped_column(primary_key=True)
    expires_at: Mapped[datetime]


Index("ix_revoked_tokens_expires_at", RevokedToken.expires_at)


class UserCredential(Base):
    __tablename__ = "user_credentials"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    password_hash: Mapped[str]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class AuthSession(Created, Base):
    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    refresh_hash: Mapped[str] = mapped_column(unique=True)
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    replaced_at: Mapped[datetime | None]
    user_agent: Mapped[str | None] = mapped_column(String(500))


class LoginCode(Base):
    __tablename__ = "login_codes"

    code_hash: Mapped[str] = mapped_column(primary_key=True)
    email: Mapped[str]  # stored lower-cased
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]


class OAuthIdentity(Created, Base):
    __tablename__ = "oauth_identities"
    __table_args__ = (CheckConstraint("provider in ('google','github')", name="provider_known"),)

    provider: Mapped[str] = mapped_column(primary_key=True)
    subject: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))


class RateHit(Base):
    """One counted attempt. Rate-limit buckets read it; keys are hashes the backend computed."""

    __tablename__ = "rate_hits"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    bucket: Mapped[str] = mapped_column(String(40))
    key_hash: Mapped[str] = mapped_column(String(64))
    hit_at: Mapped[datetime] = mapped_column(server_default=func.now())


Index("ix_rate_hits_bucket_key_hash_hit_at", RateHit.bucket, RateHit.key_hash, RateHit.hit_at)


class AiRun(Base):
    """A paused AI run (waiting for plan approval or human review) and its review decision.

    The AI engine owns the graph state; the backend records the review. No foreign keys: a
    stale run is harmless after its task is hard-deleted.
    """

    __tablename__ = "ai_runs"

    task_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    workspace_id: Mapped[uuid.UUID]
    project_id: Mapped[uuid.UUID]
    agent_member_id: Mapped[uuid.UUID]
    requester_member_id: Mapped[uuid.UUID]
    stage: Mapped[str]
    state: Mapped[dict]
    review_status: Mapped[str] = mapped_column(server_default="pending")
    review_reason: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        one_of("stage", "plan", "review", "resuming"),
        one_of("review_status", "pending", "approved", "rejected"),
    )


Index("ix_ai_runs_workspace_id", AiRun.workspace_id)
