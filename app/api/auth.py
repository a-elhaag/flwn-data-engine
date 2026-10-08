"""Request authentication and workspace locking for the REST API.

Two kinds of caller:
- Service (X-Data-API-Key): trusted backend. May act on any workspace and run
  maintenance and admin operations. It can name the member it acts for with
  X-Acting-Member-Id (for example the human whose request it is relaying).
- Agent (Authorization: Bearer <workspace token>): locked to the token's workspace and
  limited to the token's scopes. Maintenance and admin operations are never allowed. Its member
  comes only from the signed token: the header is ignored, so an agent cannot act as someone else.

Whoever acts must be an active member of the workspace, checked on every request, so suspending
a member stops their tokens immediately.
"""

import secrets
from dataclasses import dataclass, replace
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Path, Request

from app import members
from app.api import tokens
from app.config import settings

WorkspaceId = Annotated[UUID, Path()]


@dataclass(frozen=True)
class Principal:
    subject: str
    service: bool
    workspace_id: str | None = None
    scopes: frozenset[str] = frozenset()
    member_id: str | None = None  # who is acting, when known


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
            member_id=claims.member_id,
        )
    raise HTTPException(status_code=401, detail="Invalid data API key")


def _acting_for(principal: Principal, workspace_id: UUID, header: str | None) -> Principal:
    """A service caller may name the member it acts for; that member must be active here."""
    if not header:
        return principal
    try:
        member = str(UUID(header))
    except ValueError:
        raise HTTPException(status_code=422, detail="X-Acting-Member-Id must be a UUID") from None
    if not members.is_active(str(workspace_id), member):
        raise HTTPException(
            status_code=422, detail="Acting member not found or not active in this workspace"
        )
    return replace(principal, member_id=member)


def service_only(
    x_data_api_key: Annotated[str | None, Header()] = None,
) -> Principal:
    if not _service_key_valid(x_data_api_key):
        raise HTTPException(status_code=401, detail="Invalid data API key")
    return Principal(subject="service", service=True)


def allow(scope: str | None):
    """Dependency factory. scope=None means service callers only."""

    def dependency(
        request: Request,
        workspace_id: WorkspaceId,
        x_data_api_key: Annotated[str | None, Header()] = None,
        authorization: Annotated[str | None, Header()] = None,
        x_acting_member_id: Annotated[str | None, Header()] = None,
    ) -> Principal:
        principal = _principal(x_data_api_key, authorization)
        if principal.service:
            principal = _acting_for(principal, workspace_id, x_acting_member_id)
        else:
            if scope is None:
                raise HTTPException(status_code=403, detail="Service credentials required")
            if principal.workspace_id != str(workspace_id):
                raise HTTPException(status_code=403, detail="Token not valid for workspace")
            if scope not in principal.scopes:
                raise HTTPException(status_code=403, detail=f"Missing scope {scope}")
            if principal.member_id and not members.is_active(
                str(workspace_id), principal.member_id
            ):
                raise HTTPException(
                    status_code=403, detail="Member is not active in this workspace"
                )
        request.state.principal = principal  # the services built for this request read it
        return principal

    return Depends(dependency)
