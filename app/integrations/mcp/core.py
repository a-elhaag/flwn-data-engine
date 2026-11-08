"""Shared plumbing for the MCP tool modules: registration, token checks, guarded execution."""

import logging
import uuid
from collections.abc import Callable
from typing import Annotated, Any

import anyio
from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from app.api import tokens
from app.api.tokens import Claims
from app.domain.atoms.service import AtomError, AtomNotFound
from app.domain.identity import viewer as members
from app.domain.memory.errors import MaintenanceBusy, MemoryNotFound, WorkspaceNotFound

logger = logging.getLogger(__name__)


Text = Annotated[str, Field(min_length=1, max_length=20000, pattern=r"\S")]
Label = Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
MemoryId = Annotated[str, Field(description="Memory id (UUID) from recall or browse.")]


_SPECS: list[tuple[Callable, dict]] = []


def _tool(**spec):
    def register(fn):
        _SPECS.append((fn, spec))
        return fn

    return register


def _claims(ctx: Context, scope: str) -> Claims:
    header = ctx.request_context.request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise PermissionError("Missing bearer token")
    try:
        claims = tokens.verify(header[7:].strip())
    except tokens.TokenError:
        raise PermissionError("Invalid or expired token") from None
    if scope not in claims.scopes:
        raise PermissionError(f"Token lacks scope {scope}")
    return claims


async def _guarded(ctx: Context, scope: str, tool: str, fn: Callable[[Claims], Any]):
    """Run `fn` for the token's workspace, after checking the scope and that the member is active."""
    try:
        claims = _claims(ctx, scope)
    except PermissionError as exc:
        raise PermissionError(f"denied: {exc}") from None
    logger.info("mcp: tool=%s workspace=%s subject=%s", tool, claims.workspace_id, claims.subject)

    def work():
        from app.db.atom_access import AtomContext, bind_context, validate_context

        context = None
        if claims.atom_id:
            context = AtomContext(
                claims.workspace_id,
                claims.atom_id,
                claims.run_id,
                claims.member_id,
                claims.ctx or "",
            )
            validate_context(
                context,
                allow_finished=tool
                in (
                    "atom_run_finish",
                    "atom_approval_get",
                    "atom_approval_claim",
                    "atom_approval_outcome",
                ),
            )
        for acting in (claims.member_id, claims.obo):  # an assistant and the human it acts for
            if acting and not members.is_active(claims.workspace_id, acting):
                raise PermissionError("Member is not active in this workspace")
        with bind_context(context):
            return fn(claims)

    try:
        return await anyio.to_thread.run_sync(work)
    except MemoryNotFound:
        raise ValueError("Memory not found in this workspace") from None
    except WorkspaceNotFound:
        raise ValueError("Workspace not found") from None
    except MaintenanceBusy:
        raise ValueError("Maintenance is already running for this workspace") from None
    except PermissionError as exc:
        raise PermissionError(f"denied: {exc}") from None
    except AtomNotFound as exc:
        raise ValueError(f"not_found: {exc}") from None
    except AtomError as exc:
        raise ValueError(f"{exc.code}: {exc}") from None


def _id(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise ValueError("id must be a memory UUID from recall or browse") from None


READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITES = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
DESTROYS = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)
