"""Liveness, readiness, and agent token minting."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response

from app import members
from app.api import revocation, tokens
from app.api.auth import _principal, service_only
from app.api.schemas import RevocationRequest, TokenRequest, TokenResponse
from app.health import health_check

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


@router.post("/auth/tokens", dependencies=[Depends(service_only)])
def mint_token(request: TokenRequest) -> TokenResponse:
    workspace_id = str(request.workspace_id)
    member_id = str(request.member_id) if request.member_id else None
    if member_id and not members.is_active(workspace_id, member_id):
        raise HTTPException(
            status_code=422, detail="Member not found or not active in this workspace"
        )
    subject = request.subject
    owner = members.user_id(workspace_id, member_id) if member_id else None
    if owner:  # a human's token is about that person: its subject is their user id
        if "subject" not in request.model_fields_set:
            subject = owner
        elif subject != owner:
            raise HTTPException(status_code=422, detail="subject must be the member's user id")
    from app.db.atom_access import (
        AtomAccessError,
        AtomContext,
        atom_kind,
        is_atom_member,
        validate_context,
    )

    atom_id = str(request.atom_id) if request.atom_id else None
    run_id = str(request.run_id) if request.run_id else None
    ctx = None
    try:
        if atom_id or run_id:
            if not (member_id and atom_id and run_id):
                raise tokens.TokenError("atom tokens require atom_id, run_id and member_id")
            validate_context(AtomContext(workspace_id, atom_id, run_id, member_id))
            ctx = atom_kind(workspace_id, atom_id)  # from the atom row, never from the request
        elif member_id and is_atom_member(workspace_id, member_id):
            raise tokens.TokenError("Atom members require run-scoped tokens")
        ttl_seconds = request.ttl_seconds
        if atom_id and "ttl_seconds" not in request.model_fields_set:
            ttl_seconds = 900
        token, expires_at = tokens.mint(
            workspace_id,
            set(request.scopes),
            subject,
            ttl_seconds,
            member_id,
            atom_id=atom_id,
            run_id=run_id,
            ctx=ctx,
        )
    except AtomAccessError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except tokens.TokenError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return TokenResponse(token=token, expires_at=expires_at)


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
