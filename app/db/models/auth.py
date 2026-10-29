"""Token deny list: a jti listed here is refused until the token would have expired anyway."""

from datetime import datetime

from sqlalchemy import Index
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RevokedToken(Base):
    __tablename__ = "revoked_tokens"

    jti: Mapped[str] = mapped_column(primary_key=True)
    expires_at: Mapped[datetime]


Index("ix_revoked_tokens_expires_at", RevokedToken.expires_at)
