"""Atom administration tools. A human member token with `atoms:admin` is required."""

import logging
import uuid
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import Context

from app.api import tokens
from app.api.schemas.atoms import (
    AtomVersionBody,
    AttachmentState,
    ConfigureAtom,
    ConnectionBody,
    CreateAtom,
    GrantBody,
    ScheduleBody,
    SkillAttachment,
)
from app.api.tokens import Claims
from app.integrations.mcp.core import DESTROYS, READ_ONLY, WRITES, _guarded, _tool

logger = logging.getLogger(__name__)


# Atom administration.
# The same operations as the REST routes under /workspaces/{id}/atoms, for MCP clients such as an
# assistant that builds atoms for a human. A human member's token with `atoms:admin` is required,
# and AtomService applies the same role rules (owner/admin, or a member's own personal atom).
# Run tokens never hold `atoms:admin`, so an atom cannot administer itself or another atom.


def _admin_service(claims: Claims):
    from app.domain.atoms.service import AtomService

    if claims.atom_id:
        raise PermissionError("Atom administration needs a human member token, not a run token")
    return AtomService(claims.workspace_id, actor=claims.member_id)


async def _admin(ctx: Context, tool: str, fn: Callable[[Any], Any]):
    return await _guarded(ctx, tokens.SCOPE_ATOMS_ADMIN, tool, lambda c: fn(_admin_service(c)))


@_tool(
    name="atom_create",
    annotations=WRITES,
    description="Create a draft atom. Owners and admins set the daily caps; a plain member may create only their own personal atom, at default caps. Then add a version, grants, connections and a schedule, and activate.",
)
async def atom_create(body: CreateAtom, ctx: Context) -> dict:
    return await _admin(ctx, "atom_create", lambda s: s.create(**body.model_dump(mode="json")))


@_tool(
    name="atom_list",
    annotations=READ_ONLY,
    description="List the atoms this member may administer.",
)
async def atom_list(ctx: Context) -> dict:
    return {"atoms": await _admin(ctx, "atom_list", lambda s: s.list())}


@_tool(
    name="atom_get",
    annotations=READ_ONLY,
    description="One atom with its active version, grants, schedules and connections.",
)
async def atom_get(atom_id: uuid.UUID, ctx: Context) -> dict:
    return await _admin(ctx, "atom_get", lambda s: s.get(str(atom_id)))


@_tool(
    name="atom_configure",
    annotations=WRITES,
    description="Change name, description, model tier or daily caps (caps need an owner or admin). status=paused pauses the atom; status=killed stops it for good and disables its schedules.",
)
async def atom_configure(atom_id: uuid.UUID, body: ConfigureAtom, ctx: Context) -> dict:
    return await _admin(
        ctx,
        "atom_configure",
        lambda s: s.configure(str(atom_id), **body.model_dump(mode="json", exclude_unset=True)),
    )


@_tool(
    name="atom_delete",
    annotations=DESTROYS,
    description="Soft-delete an atom and stop it. Cannot be undone through this API.",
)
async def atom_delete(atom_id: uuid.UUID, ctx: Context) -> dict:
    await _admin(ctx, "atom_delete", lambda s: s.delete(str(atom_id)))
    return {"deleted": True}


@_tool(
    name="atom_version_create",
    annotations=WRITES,
    description="Add a candidate version (instructions, policy, tools) to an atom. It runs only after atom_activate.",
)
async def atom_version_create(atom_id: uuid.UUID, body: AtomVersionBody, ctx: Context) -> dict:
    return await _admin(
        ctx, "atom_version_create", lambda s: s.propose(str(atom_id), **body.model_dump())
    )


@_tool(
    name="atom_activate",
    annotations=WRITES,
    description="Make a version the atom's active one. Human confirmation is the caller's job: confirm with the person first.",
)
async def atom_activate(atom_id: uuid.UUID, version_id: uuid.UUID, ctx: Context) -> dict:
    return await _admin(ctx, "atom_activate", lambda s: s.activate(str(atom_id), str(version_id)))


@_tool(
    name="atom_rollback",
    annotations=WRITES,
    description="Make an earlier version the active one again.",
)
async def atom_rollback(atom_id: uuid.UUID, version_id: uuid.UUID, ctx: Context) -> dict:
    return await _admin(ctx, "atom_rollback", lambda s: s.rollback(str(atom_id), str(version_id)))


@_tool(
    name="atom_grant_set",
    annotations=WRITES,
    description="Give the atom access to a resource at a level (summary, read, write). Repeating it replaces the level.",
)
async def atom_grant_set(atom_id: uuid.UUID, body: GrantBody, ctx: Context) -> dict:
    return await _admin(
        ctx,
        "atom_grant_set",
        lambda s: s.set_grant(str(atom_id), **body.model_dump(mode="json")),
    )


@_tool(name="atom_grant_remove", annotations=DESTROYS, description="Revoke one grant from an atom.")
async def atom_grant_remove(atom_id: uuid.UUID, grant_id: uuid.UUID, ctx: Context) -> dict:
    await _admin(ctx, "atom_grant_remove", lambda s: s.revoke_grant(str(atom_id), str(grant_id)))
    return {"revoked": True}


@_tool(
    name="atom_schedule_set",
    annotations=WRITES,
    description="Add a schedule (exactly one of cron or interval_minutes), or replace one by passing schedule_id.",
)
async def atom_schedule_set(
    atom_id: uuid.UUID, body: ScheduleBody, ctx: Context, schedule_id: uuid.UUID | None = None
) -> dict:
    extra = {"schedule_id": str(schedule_id)} if schedule_id else {}
    return await _admin(
        ctx,
        "atom_schedule_set",
        lambda s: s.set_schedule(str(atom_id), **extra, **body.model_dump()),
    )


@_tool(
    name="atom_schedule_delete",
    annotations=DESTROYS,
    description="Delete one schedule from an atom.",
)
async def atom_schedule_delete(atom_id: uuid.UUID, schedule_id: uuid.UUID, ctx: Context) -> dict:
    return await _admin(
        ctx,
        "atom_schedule_delete",
        lambda s: s.delete_schedule(str(atom_id), str(schedule_id)),
    )


@_tool(
    name="atom_connection_set",
    annotations=WRITES,
    description="Attach a Composio connection (pinned toolkit version, permission ceiling, allowed tool slugs), or replace one by passing connection_id.",
)
async def atom_connection_set(
    atom_id: uuid.UUID, body: ConnectionBody, ctx: Context, connection_id: uuid.UUID | None = None
) -> dict:
    extra = {"connection_id": str(connection_id)} if connection_id else {}
    return await _admin(
        ctx,
        "atom_connection_set",
        lambda s: s.set_connection(str(atom_id), **extra, **body.model_dump()),
    )


@_tool(
    name="atom_connection_delete",
    annotations=DESTROYS,
    description="Remove one connection from an atom.",
)
async def atom_connection_delete(
    atom_id: uuid.UUID, connection_id: uuid.UUID, ctx: Context
) -> dict:
    return await _admin(
        ctx,
        "atom_connection_delete",
        lambda s: s.delete_connection(str(atom_id), str(connection_id)),
    )


def _admin_skills(claims: Claims):
    from app.domain.atoms.skills import SkillService

    if claims.atom_id:
        raise PermissionError("Atom administration needs a human member token, not a run token")
    return SkillService(claims.workspace_id, actor=claims.member_id)


@_tool(
    name="atom_skill_attach",
    annotations=WRITES,
    description="Attach an exact skill version to an atom (an administrator acting on any atom they manage). Only within the atom's connection ceilings and allowed tool slugs.",
)
async def atom_skill_attach(atom_id: uuid.UUID, body: SkillAttachment, ctx: Context) -> dict:
    return await _guarded(
        ctx,
        tokens.SCOPE_ATOMS_ADMIN,
        "atom_skill_attach",
        lambda c: _admin_skills(c).attach(
            str(body.skill_id),
            skill_version=body.skill_version,
            catalog=body.catalog,
            atom_id=str(atom_id),
        ),
    )


@_tool(
    name="atom_skill_enable",
    annotations=WRITES,
    description="Turn one attached skill on or off for an atom.",
)
async def atom_skill_enable(
    atom_id: uuid.UUID, attachment_id: uuid.UUID, body: AttachmentState, ctx: Context
) -> dict:
    return await _guarded(
        ctx,
        tokens.SCOPE_ATOMS_ADMIN,
        "atom_skill_enable",
        lambda c: _admin_skills(c).set_attachment(
            str(attachment_id), enabled=body.enabled, atom_id=str(atom_id)
        ),
    )
