"""The auth store: password hashes, refresh sessions, login codes and OAuth links.

Service key only, and never a workspace: the backend hashes every secret and sends hashes here.
Plain functions over a session; the route opens it with service_session().
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.db.models.auth import AuthSession, LoginCode, OAuthIdentity, UserCredential
from app.db.models.identity import User
from app.domain.error_codes import DomainError
from app.domain.identity.users import UserNotFound, email_matches


class AuthStoreError(DomainError):
    """A client error with the HTTP status the route answers with."""

    status = 404


class CredentialNotFound(AuthStoreError, LookupError):
    code = "credential_not_found"


class SessionNotFound(AuthStoreError, LookupError):
    code = "session_not_found"


class RefreshInvalid(AuthStoreError):
    code = "refresh_invalid"
    status = 401


class RefreshExpired(AuthStoreError):
    code = "refresh_expired"
    status = 401


class CodeInvalid(AuthStoreError, LookupError):
    code = "code_invalid"


class OAuthIdentityNotFound(AuthStoreError, LookupError):
    code = "oauth_identity_not_found"


def _require_user(session, user_id):
    if session.get(User, user_id) is None:
        raise UserNotFound("user not found", code="user_not_found")


def set_credential(session, user_id: uuid.UUID, password_hash: str) -> None:
    _require_user(session, user_id)
    stmt = insert(UserCredential).values(
        user_id=user_id, password_hash=password_hash, updated_at=func.now()
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=[UserCredential.user_id],
            set_={"password_hash": stmt.excluded.password_hash, "updated_at": func.now()},
        )
    )


def delete_credential(session, user_id: uuid.UUID) -> None:
    """Idempotent: a user with no password hash is not an error."""
    session.execute(delete(UserCredential).where(UserCredential.user_id == user_id))


def get_credential(session, email: str) -> dict:
    row = session.execute(
        select(UserCredential.user_id, UserCredential.password_hash)
        .join(User, User.id == UserCredential.user_id)
        .where(email_matches(email))
    ).first()
    if row is None:
        raise CredentialNotFound("credential not found")
    return {"user_id": row.user_id, "password_hash": row.password_hash}


def create_session(
    session, user_id: uuid.UUID, refresh_hash: str, expires_at: datetime, user_agent: str | None
) -> uuid.UUID:
    _require_user(session, user_id)
    row = AuthSession(
        user_id=user_id, refresh_hash=refresh_hash, expires_at=expires_at, user_agent=user_agent
    )
    session.add(row)
    session.flush()
    return row.id


def find_session(session, refresh_hash: str) -> dict:
    """A session that is live: not revoked, not replaced, not expired."""
    row = session.execute(
        select(AuthSession.id, AuthSession.user_id, AuthSession.expires_at).where(
            AuthSession.refresh_hash == refresh_hash,
            AuthSession.revoked_at.is_(None),
            AuthSession.replaced_at.is_(None),
            AuthSession.expires_at > func.now(),
        )
    ).first()
    if row is None:
        raise SessionNotFound("session not found")
    return {"id": row.id, "user_id": row.user_id, "expires_at": row.expires_at}


def rotate_session(
    session, refresh_hash: str, new_refresh_hash: str, expires_at: datetime
) -> tuple[uuid.UUID, uuid.UUID] | None:
    """Swap a refresh token for a new one. Returns (new id, user id), or None on reuse.

    On reuse every session of the user is revoked and None is returned, so the caller still
    commits the revocation and then answers 409. Raising here would roll it back.
    """
    row = session.execute(
        select(AuthSession).where(AuthSession.refresh_hash == refresh_hash).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise RefreshInvalid("refresh token is not valid")
    if row.replaced_at is not None or row.revoked_at is not None:
        session.execute(
            update(AuthSession)
            .where(AuthSession.user_id == row.user_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=func.now())
        )
        return None
    if row.expires_at <= datetime.now(UTC):
        raise RefreshExpired("refresh token has expired")
    row.replaced_at = datetime.now(UTC)
    fresh = AuthSession(user_id=row.user_id, refresh_hash=new_refresh_hash, expires_at=expires_at)
    session.add(fresh)
    session.flush()
    return fresh.id, row.user_id


def revoke_session(session, refresh_hash: str) -> None:
    session.execute(
        update(AuthSession)
        .where(AuthSession.refresh_hash == refresh_hash, AuthSession.revoked_at.is_(None))
        .values(revoked_at=func.now())
    )


def revoke_user_sessions(session, user_id: uuid.UUID) -> None:
    session.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=func.now())
    )


def create_login_code(session, code_hash: str, email: str, expires_at: datetime) -> None:
    session.add(LoginCode(code_hash=code_hash, email=email.lower(), expires_at=expires_at))


def consume_login_code(session, code_hash: str) -> str:
    """Single use, even under races: the UPDATE only matches a code nobody has used yet."""
    email = session.execute(
        update(LoginCode)
        .where(
            LoginCode.code_hash == code_hash,
            LoginCode.used_at.is_(None),
            LoginCode.expires_at > func.now(),
        )
        .values(used_at=func.now())
        .returning(LoginCode.email)
    ).scalar_one_or_none()
    if email is None:
        raise CodeInvalid("login code is not valid")
    return email


def upsert_oauth_identity(session, provider: str, subject: str, user_id: uuid.UUID) -> None:
    _require_user(session, user_id)
    stmt = insert(OAuthIdentity).values(provider=provider, subject=subject, user_id=user_id)
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=[OAuthIdentity.provider, OAuthIdentity.subject],
            set_={"user_id": stmt.excluded.user_id},
        )
    )


def get_oauth_identity(session, provider: str, subject: str) -> dict:
    user_id = session.scalar(
        select(OAuthIdentity.user_id).where(
            OAuthIdentity.provider == provider, OAuthIdentity.subject == subject
        )
    )
    if user_id is None:
        raise OAuthIdentityNotFound("oauth identity not found")
    return {"user_id": user_id}
