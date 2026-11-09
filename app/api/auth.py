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

import logging
import secrets
import threading
import time
from dataclasses import dataclass, replace
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Path, Request

from app.api import tokens
from app.api.errors import api_error, code_of
from app.core.config import settings
from app.db.session import bind_viewer
from app.domain.identity import viewer as members

WorkspaceId = Annotated[UUID, Path()]
log = logging.getLogger(__name__)


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
    """The current key or, during a rotation, the next one. Both are compared in constant time."""
    if key is None:
        return False
    given = key.encode()
    current = secrets.compare_digest(given, settings.DATA_API_KEY.encode())
    upcoming = bool(settings.DATA_API_KEY_NEXT) and secrets.compare_digest(
        given, settings.DATA_API_KEY_NEXT.encode()
    )
    return current or upcoming


_REJECT_LOG_EVERY = 60.0  # seconds: at most one warning per source per minute
_reject_log_lock = threading.Lock()
_reject_log: dict[str, tuple[float, int]] = {}  # source -> (last logged at, suppressed since)


def _clean(value: str | None, limit: int = 200) -> str:
    """A log-safe rendering of caller-controlled text: no line breaks or control characters."""
    if not value:
        return "-"
    return value[:limit].encode("unicode_escape").decode("ascii")


def _reject_service_key(request: Request | None, key: str | None) -> None:
    """Log a refused service key with the caller's address (never the key itself).

    peer is the socket address, which behind the ingress proxy is the proxy; forwarded_for is the
    X-Forwarded-For header, logged as given because the engine cannot verify it. Both and the path
    are escaped so a caller cannot inject log lines, and each source logs at most once a minute
    (with a count of what was suppressed) so a flood cannot fill the logs.
    """
    if not key:  # a missing key is an ordinary unauthenticated call, a wrong one is worth an alert
        return
    peer = request.client.host if request is not None and request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for") if request is not None else None
    source = f"{peer}|{forwarded or ''}"
    now = time.monotonic()
    with _reject_log_lock:
        last, suppressed = _reject_log.get(source, (0.0, 0))
        if now - last < _REJECT_LOG_EVERY:
            _reject_log[source] = (last, suppressed + 1)
            return
        if len(_reject_log) > 10_000:
            _reject_log.clear()
        _reject_log[source] = (now, 0)
    log.warning(
        "service key rejected: peer=%s forwarded_for=%s path=%s suppressed=%d",
        _clean(peer),
        _clean(forwarded),
        _clean(_path(request)),
        suppressed,
    )


def _path(request: Request | None) -> str:
    return request.url.path if request is not None else "?"


def _principal(
    x_data_api_key: str | None, authorization: str | None, request: Request | None = None
) -> Principal:
    if _service_key_valid(x_data_api_key):
        return Principal(subject="service", service=True)
    _reject_service_key(request, x_data_api_key)
    if authorization and authorization.lower().startswith("bearer "):
        try:
            claims = tokens.verify(authorization[7:].strip())
        except tokens.TokenError:
            raise api_error(401, "token_invalid", "Invalid or expired token") from None
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
    raise api_error(401, "service_key_invalid", "Invalid data API key")


def _acting_for(principal: Principal, workspace_id: UUID, header: str | None) -> Principal:
    """A service caller may name the member it acts for; that member must be active here."""
    if not header:
        return principal
    try:
        member = str(UUID(header))
    except ValueError:
        raise api_error(
            422,
            "acting_member_invalid",
            "X-Acting-Member-Id must be a UUID",
            {"X-Acting-Member-Id": "must be a UUID"},
        ) from None
    if not members.is_active(str(workspace_id), member):
        raise api_error(
            422,
            "acting_member_not_found",
            "Acting member not found or not active in this workspace",
            {"X-Acting-Member-Id": "not an active member of this workspace"},
        )
    return replace(principal, member_id=member)


def service_only(
    request: Request,
    x_data_api_key: Annotated[str | None, Header()] = None,
) -> Principal:
    if not _service_key_valid(x_data_api_key):
        _reject_service_key(request, x_data_api_key)
        raise api_error(401, "service_key_invalid", "Invalid data API key")
    return Principal(subject="service", service=True)


def allow(
    scope: str | None,
    *,
    allow_finished: bool = False,
    write_scope: str | None = None,
):
    """Dependency factory. scope=None means service callers only.

    write_scope: the scope for requests that change data (any method but GET and HEAD); scope
    then covers reads only. The service key passes every scope check.
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
        principal = _principal(x_data_api_key, authorization, request)
        if principal.service:
            principal = _acting_for(principal, workspace_id, x_acting_member_id)
        else:
            if needed is None:
                raise api_error(403, "service_credentials_required", "Service credentials required")
            if principal.workspace_id != str(workspace_id):
                raise api_error(403, "token_workspace_mismatch", "Token not valid for workspace")
            if needed not in principal.scopes and tokens.SCOPE_ADMIN not in principal.scopes:
                raise api_error(403, "scope_missing", f"Missing scope {needed}")
            # An assistant acts for a human: both must still be active, so suspending the human
            # stops the assistant's next request too.
            for acting in (principal.member_id, principal.obo):
                if acting and not members.is_active(str(workspace_id), acting):
                    raise api_error(
                        403, "member_inactive", "Member is not active in this workspace"
                    )
        from app.db.atom_access import (
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
                    raise api_error(403, code_of(exc, "atom_access_denied"), str(exc)) from exc
            elif principal.member_id and is_atom_member(str(workspace_id), principal.member_id):
                raise api_error(
                    403, "atom_token_required", "Atom members require run-scoped tokens"
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
        raise api_error(403, "member_token_required", "A member-bound token is required to write")
    if requested is not None and requested != mine:
        raise api_error(
            403, "author_mismatch", "author_id must be the acting member (X-Acting-Member-Id)"
        )
    return mine


def actor(request: Request) -> str | None:
    """The member id to record as the author of a change."""
    mine = viewer(request).member_id
    return str(mine) if mine else None
