"""Liveness, readiness, token revocation and session checks."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response

from app.api import revocation
from app.api.auth import _principal, service_only
from app.api.schemas.common import RevocationRequest
from app.core.health import health_check
from app.domain.identity import viewer as members

router = APIRouter()


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", dependencies=[Depends(service_only)])
def readyz(response: Response) -> dict[str, bool]:
    status = health_check()
    if not all(status.values()):
        response.status_code = 503
    return status


@router.post("/auth/revocations", status_code=204, dependencies=[Depends(service_only)])
def revoke_token(request: RevocationRequest) -> None:
    """The backend lists a token's jti so it stops working now. Safe to repeat."""
    revocation.revoke(request.jti, request.expires_at)


@router.get("/auth/session")
def token_session(authorization: Annotated[str | None, Header()] = None) -> dict:
    """Is this bearer token still good? Signature, expiry, revocation and member status are all
    checked. The Atomizer service asks this on chat calls it does not forward to the data engine."""
    principal = _principal(None, authorization)  # 401 when invalid, expired or revoked
    from app.db.atom_access import is_atom_member

    # Same gate as allow(): an atom is only ever vouched for by the routes it calls, with its run checked.
    if principal.atom_id or (
        principal.member_id and is_atom_member(principal.workspace_id, principal.member_id)
    ):
        raise HTTPException(status_code=403, detail="Atom tokens are not checked here")
    for acting in (principal.member_id, principal.obo):
        if acting and not members.is_active(principal.workspace_id, acting):
            raise HTTPException(status_code=403, detail="Member is not active in this workspace")
    return {
        "workspace_id": principal.workspace_id,
        "member_id": principal.member_id,
        "typ": principal.typ,
        "jti": principal.jti,
    }
