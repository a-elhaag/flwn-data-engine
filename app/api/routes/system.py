"""Liveness, readiness, and agent token minting."""

from fastapi import APIRouter, Depends, HTTPException, Response

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
    try:
        token, expires_at = tokens.mint(
            str(request.workspace_id), set(request.scopes), request.subject, request.ttl_seconds
        )
    except tokens.TokenError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return TokenResponse(token=token, expires_at=expires_at)
