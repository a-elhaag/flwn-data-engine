"""Liveness, readiness, and agent token minting."""

from fastapi import APIRouter, Depends, HTTPException, Response

from app import members
from app.api import tokens
from app.api.auth import service_only
from app.api.schemas import TokenRequest, TokenResponse
from app.clients.health import health_check

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
    from app.atoms.access import AtomAccessError, AtomContext, is_atom_member, validate_context

    atom_id = str(request.atom_id) if request.atom_id else None
    run_id = str(request.run_id) if request.run_id else None
    try:
        if atom_id or run_id:
            if not (member_id and atom_id and run_id):
                raise tokens.TokenError("atom tokens require atom_id, run_id and member_id")
            validate_context(AtomContext(workspace_id, atom_id, run_id, member_id))
        elif member_id and is_atom_member(workspace_id, member_id):
            raise tokens.TokenError("Atom members require run-scoped tokens")
        ttl_seconds = request.ttl_seconds
        if atom_id and "ttl_seconds" not in request.model_fields_set:
            ttl_seconds = 900
        token, expires_at = tokens.mint(
            workspace_id,
            set(request.scopes),
            request.subject,
            ttl_seconds,
            member_id,
            atom_id=atom_id,
            run_id=run_id,
        )
    except AtomAccessError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except tokens.TokenError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return TokenResponse(token=token, expires_at=expires_at)
