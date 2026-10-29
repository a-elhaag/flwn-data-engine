"""Request authentication and workspace locking for the REST API.

Two kinds of caller:
- Service (X-Data-API-Key): trusted backend. May act on any workspace and run
  maintenance and admin operations. It can name the member it acts for with
  X-Acting-Member-Id (for example the human whose request it is relaying).
- Token (Authorization: Bearer <workspace token>, signed by the backend): typ is human, agent,
  run or service. Locked to the token's workspace and limited to the token's scopes. Service-only
  operations are never allowed; atom administration needs a human token with atoms:admin, and
  cleanup needs the memory:maintain scope. Its member comes only
  from the signed token: the header is ignored, so a token cannot act as someone else. An agent
  token also names the human it acts for (obo); both must be active.

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
from app.db.session import bind_viewer

WorkspaceId = Annotated[UUID, Path()]


@dataclass(frozen=True)
class Principal:
    subject: str
    service: bool
    workspace_id: str | None = None
    scopes: frozenset[str] = frozenset()
    member_id: str | None = None  # who is acting, when known
    atom_id: str | None = None
    run_id: str | None = None
    ctx: str | None = None
    typ: str | None = None  # human, agent, run or service; None for the service key
    jti: str | None = None
    obo: str | None = None  # the human an agent acts for
    act: str | None = None  # the service that forwarded the call


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
            atom_id=claims.atom_id,
            run_id=claims.run_id,
            ctx=claims.ctx,
            typ=claims.typ,
            jti=claims.jti,
            obo=claims.obo,
            act=claims.act,
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


def allow(
    scope: str | None,
    *,
    allow_finished: bool = False,
    write_scope: str | None = None,
):
    """Dependency factory. scope=None means service callers only.

    write_scope: the scope for requests that change data (any method but GET and HEAD); scope
    then covers reads only. The service key passes every scope check (D36).
    A token holding the `admin` scope passes every scope check except scope=None.
    """

    async def dependency(
        request: Request,
        workspace_id: WorkspaceId,
        x_data_api_key: Annotated[str | None, Header()] = None,
        authorization: Annotated[str | None, Header()] = None,
        x_acting_member_id: Annotated[str | None, Header()] = None,
    ) -> Principal:
        needed = write_scope if write_scope and request.method not in ("GET", "HEAD") else scope
        principal = _principal(x_data_api_key, authorization)
        if principal.service:
            principal = _acting_for(principal, workspace_id, x_acting_member_id)
        else:
            if needed is None:
                raise HTTPException(status_code=403, detail="Service credentials required")
            if principal.workspace_id != str(workspace_id):
                raise HTTPException(status_code=403, detail="Token not valid for workspace")
            if needed not in principal.scopes and tokens.SCOPE_ADMIN not in principal.scopes:
                raise HTTPException(status_code=403, detail=f"Missing scope {needed}")
            # An assistant acts for a human: both must still be active, so suspending the human
            # stops the assistant's next request too.
            for acting in (principal.member_id, principal.obo):
                if acting and not members.is_active(str(workspace_id), acting):
                    raise HTTPException(
                        status_code=403, detail="Member is not active in this workspace"
                    )
        from app.atoms.access import (
            AtomAccessError,
            AtomContext,
            bind_context,
            is_atom_member,
            validate_context,
        )

        context = None
        if not principal.service:
            if principal.atom_id:
                context = AtomContext(
                    str(workspace_id),
                    principal.atom_id,
                    principal.run_id,
                    principal.member_id,
                    principal.ctx or "",
                )
                try:
                    validate_context(context, allow_finished=allow_finished)
                except AtomAccessError as exc:
                    raise HTTPException(status_code=403, detail=str(exc)) from exc
            elif principal.member_id and is_atom_member(str(workspace_id), principal.member_id):
                raise HTTPException(
                    status_code=403, detail="Atom members require run-scoped tokens"
                )
        request.state.principal = principal
        # Async yield keeps the ContextVar in the event-loop task copied into sync endpoints.
        # Each non-admin member sees only their own private rows (db/sql/60_member_privacy.sql).
        # The service key, an admin token and atoms (own rules above) have no per-member limit.
        limited = not (
            context
            or (principal.service and not principal.member_id)
            or tokens.SCOPE_ADMIN in principal.scopes
        )
        with bind_context(context), bind_viewer((principal.member_id or "") if limited else None):
            yield principal

    return Depends(dependency)


def viewer(request: Request) -> members.Viewer:
    """The caller as the services see them. Needs the permission check to have run."""
    principal = request.state.principal
    return members.Viewer(
        UUID(principal.member_id) if principal.member_id else None,
        principal.service or tokens.SCOPE_ADMIN in principal.scopes,  # the backend is trusted
    )


def author(request: Request, requested: UUID | None) -> UUID | None:
    """The member a write is attributed to: the caller's own, never one the body names.

    A token must be bound to a member. The service key may act for a member through
    X-Acting-Member-Id; without it the write has no author.
    """
    mine = viewer(request).member_id
    if mine is None and not request.state.principal.service:
        raise HTTPException(status_code=403, detail="A member-bound token is required to write")
    if requested is not None and requested != mine:
        raise HTTPException(
            status_code=403, detail="author_id must be the acting member (X-Acting-Member-Id)"
        )
    return mine


def actor(request: Request) -> str | None:
    """The member id to record as the author of a change."""
    mine = viewer(request).member_id
    return str(mine) if mine else None
