"""The jti deny list. A listed token is refused until its own expiry, then the row is dropped."""

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import sessionmaker

from app.db import session as db
from app.db.models.auth import RevokedToken


def _session():
    return sessionmaker(db.engine(), expire_on_commit=False)()


def is_revoked(jti: str) -> bool:
    with _session() as session:
        return session.scalar(select(RevokedToken.jti).where(RevokedToken.jti == jti)) is not None


def revoke(jti: str, expires_at: int) -> None:
    """List `jti` until `expires_at` (unix seconds). Safe to repeat. Also drops expired rows."""
    now = datetime.now(UTC)
    with _session() as session, session.begin():
        session.execute(delete(RevokedToken).where(RevokedToken.expires_at < now))
        session.execute(
            insert(RevokedToken)
            .values(jti=jti, expires_at=datetime.fromtimestamp(expires_at, UTC))
            .on_conflict_do_nothing()
        )
