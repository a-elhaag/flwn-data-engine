"""Request authentication and workspace locking for the REST API.

Two kinds of caller:
- Service (X-Data-API-Key): trusted backend. May act on any workspace and run
  maintenance and admin operations.
- Agent (Authorization: Bearer <workspace token>): locked to the token's workspace and
  limited to the token's scopes. Maintenance and admin operations are never allowed.
"""

import secrets
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Path

from app.api import tokens
from app.config import settings

WorkspaceId = Annotated[UUID, Path()]


@dataclass(frozen=True)
class Principal:
    subject: str
    service: bool
    workspace_id: str | None = None
    scopes: frozenset[str] = frozenset()


def _service_key_valid(key: str | None) -> bool:
    return key is not None and secrets.compare_digest(key.encode(), settings.DATA_API_KEY.encode())


def _principal(x_data_api_key: str | None, authorization: str | None) -> Principal:
    if _service_key_valid(x_data_api_key):
        return Principal(subject="service", service=True)
    if authorization and authorization.lower().startswith("bearer "):
        try:
            claims = tokens.verify(authorization[7:].strip())
        except tokens.TokenError:
            raise HTTPException(status_code=401, detail="Invalid or expired token") from None
        return Principal(
            subject=claims.subject,
            service=False,
            workspace_id=claims.workspace_id,
            scopes=claims.scopes,
        )
    raise HTTPException(status_code=401, detail="Invalid data API key")


def service_only(
    x_data_api_key: Annotated[str | None, Header()] = None,
) -> Principal:
    if not _service_key_valid(x_data_api_key):
        raise HTTPException(status_code=401, detail="Invalid data API key")
    return Principal(subject="service", service=True)


def allow(scope: str | None):
    """Dependency factory. scope=None means service callers only."""

    def dependency(
        workspace_id: WorkspaceId,
        x_data_api_key: Annotated[str | None, Header()] = None,
        authorization: Annotated[str | None, Header()] = None,
    ) -> Principal:
        principal = _principal(x_data_api_key, authorization)
        if principal.service:
            return principal
        if scope is None:
            raise HTTPException(status_code=403, detail="Service credentials required")
        if principal.workspace_id != str(workspace_id):
            raise HTTPException(status_code=403, detail="Token not valid for workspace")
        if scope not in principal.scopes:
            raise HTTPException(status_code=403, detail=f"Missing scope {scope}")
        return principal

    return Depends(dependency)
