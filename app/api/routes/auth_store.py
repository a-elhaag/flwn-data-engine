"""Auth store routes: password hashes, refresh sessions, login codes, OAuth links (service key only)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from app.api.auth import service_only
from app.api.errors import api_error
from app.api.schemas.auth_store import (
    ConsumedOut,
    ConsumeIn,
    CredentialIn,
    CredentialOut,
    LoginCodeIn,
    OAuthIdentityIn,
    OAuthIdentityOut,
    RevokeIn,
    RotateIn,
    RotateOut,
    SessionCreated,
    SessionIn,
    SessionOut,
)
from app.db.session import service_session
from app.domain.identity import auth_store as store
from app.domain.identity.users import UserNotFound

router = APIRouter(prefix="/auth-store", dependencies=[Depends(service_only)])


def _run(fn, *args, **kwargs):
    try:
        with service_session() as session:
            return fn(session, *args, **kwargs)
    except (store.AuthStoreError, UserNotFound) as exc:
        raise api_error(getattr(exc, "status", 404), exc.code, str(exc)) from None


@router.put("/credentials/{user_id}", status_code=204)
def put_credential(user_id: UUID, body: CredentialIn):
    _run(store.set_credential, user_id, body.password_hash)
    return Response(status_code=204)


@router.delete("/credentials/{user_id}", status_code=204)
def delete_credential(user_id: UUID):
    _run(store.delete_credential, user_id)
    return Response(status_code=204)


@router.get("/credentials", response_model=CredentialOut)
def get_credential(email: Annotated[str, Query(min_length=3, max_length=320)]):
    return _run(store.get_credential, email)


@router.post("/sessions", status_code=201, response_model=SessionCreated)
def create_session(body: SessionIn):
    session_id = _run(
        store.create_session, body.user_id, body.refresh_hash, body.expires_at, body.user_agent
    )
    return SessionCreated(id=session_id)


@router.get("/sessions", response_model=SessionOut)
def find_session(refresh_hash: Annotated[str, Query(min_length=1, max_length=512)]):
    return _run(store.find_session, refresh_hash)


@router.post("/sessions/rotate", response_model=RotateOut)
def rotate_session(body: RotateIn):
    result = _run(store.rotate_session, body.refresh_hash, body.new_refresh_hash, body.expires_at)
    if result is None:  # reuse: the revocation is committed; nothing new was issued
        raise api_error(409, "refresh_reused", "refresh token was reused; every session revoked")
    session_id, user_id = result
    return RotateOut(id=session_id, user_id=user_id)


@router.post("/sessions/revoke", status_code=204)
def revoke_session(body: RevokeIn):
    _run(store.revoke_session, body.refresh_hash)
    return Response(status_code=204)


@router.post("/users/{user_id}/sessions/revoke", status_code=204)
def revoke_user_sessions(user_id: UUID):
    _run(store.revoke_user_sessions, user_id)
    return Response(status_code=204)


@router.post("/login-codes", status_code=201)
def create_login_code(body: LoginCodeIn):
    _run(store.create_login_code, body.code_hash, body.email, body.expires_at)
    return Response(status_code=201)


@router.post("/login-codes/consume", response_model=ConsumedOut)
def consume_login_code(body: ConsumeIn):
    return ConsumedOut(email=_run(store.consume_login_code, body.code_hash))


@router.put("/oauth-identities", status_code=204)
def put_oauth_identity(body: OAuthIdentityIn):
    _run(store.upsert_oauth_identity, body.provider, body.subject, body.user_id)
    return Response(status_code=204)


@router.get("/oauth-identities/{provider}/{subject}", response_model=OAuthIdentityOut)
def get_oauth_identity(provider: str, subject: str):
    return _run(store.get_oauth_identity, provider, subject)
