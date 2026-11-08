"""Tools an atom calls while it runs: load, runs, versions, skills, aggregates, approvals."""

import logging
import uuid
from datetime import datetime
from typing import Annotated, Literal

from mcp.server.fastmcp import Context
from pydantic import Field

from app.api.tokens import Claims
from app.integrations.mcp.core import READ_ONLY, WRITES, Label, Text, _guarded, _tool

logger = logging.getLogger(__name__)


def _atom_service(claims: Claims):
    from app.domain.atoms.service import AtomService

    if not claims.atom_id or not claims.run_id:
        raise PermissionError("This tool requires a run-scoped atom token")
    return AtomService(
        claims.workspace_id,
        actor=claims.member_id,
        atom_id=claims.atom_id,
        run_id=claims.run_id,
    )


@_tool(
    name="atom_load",
    annotations=READ_ONLY,
    description="Load this atom's active configuration, pinned skills, grants and schedules.",
)
async def atom_load(ctx: Context) -> dict:
    return await _guarded(
        ctx, "atoms:read", "atom_load", lambda c: _atom_service(c).load(c.atom_id)
    )


@_tool(
    name="atom_run_start",
    annotations=WRITES,
    description="Resume the token-bound scheduled run idempotently. The trusted scheduler starts the run before minting its token.",
)
async def atom_run_start(
    schedule_id: uuid.UUID,
    scheduled_for: datetime,
    idempotency_key: Label,
    ctx: Context,
) -> dict:
    return await _guarded(
        ctx,
        "atoms:run",
        "atom_run_start",
        lambda c: _atom_service(c).run_start(
            c.atom_id,
            schedule_id=str(schedule_id),
            scheduled_for=scheduled_for,
            idempotency_key=idempotency_key,
        ),
    )


@_tool(
    name="atom_run_finish",
    annotations=WRITES,
    description="Finish the bound run once, reconcile usage and advance its cursor only on success. Repeated finishes do not charge twice.",
)
async def atom_run_finish(
    status: Literal["succeeded", "failed", "canceled"],
    ctx: Context,
    output: dict | None = None,
    tokens_in: Annotated[int, Field(ge=0)] = 0,
    tokens_out: Annotated[int, Field(ge=0)] = 0,
    cost: Annotated[str, Field(strict=True, pattern=r"^[0-9]{1,6}(?:\.[0-9]{1,6})?$")] = "0",
    cursor: dict | None = None,
    actions_count: Annotated[int, Field(ge=0)] = 0,
    model: Label | None = None,
) -> dict:
    return await _guarded(
        ctx,
        "atoms:run",
        "atom_run_finish",
        lambda c: _atom_service(c).run_finish(
            c.run_id,
            status=status,
            output=output or {},
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost=cost,
            cursor=cursor,
            actions_count=actions_count,
            model=model,
        ),
    )


@_tool(
    name="atom_propose_version",
    annotations=WRITES,
    description="Propose a new candidate version for this atom. Only a human admin can activate it.",
)
async def atom_propose_version(
    instructions: Text,
    ctx: Context,
    policy: dict | None = None,
    tools: list[dict] | None = None,
) -> dict:
    return await _guarded(
        ctx,
        "atoms:propose",
        "atom_propose_version",
        lambda c: _atom_service(c).propose(
            c.atom_id,
            instructions=instructions,
            policy=policy or {},
            tools=tools or [],
        ),
    )


@_tool(
    name="atom_connection_report",
    annotations=WRITES,
    description="Report an expired, revoked or unknown own connection. Three distinct failed runs pause the atom; retries are deduplicated. Cannot activate a connection or change permissions.",
)
async def atom_connection_report(
    connection_id: uuid.UUID,
    status: Literal["expired", "revoked", "unknown"],
    ctx: Context,
    detail: Annotated[str | None, Field(max_length=2000)] = None,
) -> dict:
    return await _guarded(
        ctx,
        "atoms:run",
        "atom_connection_report",
        lambda c: _atom_service(c).connection_report(
            c.run_id,
            connection_id=str(connection_id),
            status=status,
            detail=detail,
        ),
    )


def _skill_service(claims: Claims):
    from app.domain.atoms.skills import SkillService

    return SkillService(
        claims.workspace_id, actor=claims.member_id, atom_id=claims.atom_id, run_id=claims.run_id
    )


@_tool(
    name="skills_search",
    annotations=READ_ONLY,
    description="Hybrid search of accessible skill metadata. Community results are explicitly flagged; instructions are not returned by search.",
)
async def skills_search(
    query: Label, ctx: Context, limit: Annotated[int, Field(ge=1, le=50)] = 5
) -> dict:
    return await _guarded(
        ctx,
        "skills:read",
        "skills_search",
        lambda c: {"skills": _skill_service(c).search(query, limit=limit)},
    )


@_tool(
    name="skill_write",
    annotations=WRITES,
    description="Write a versioned workspace or personal skill. Tool requirements use ref and minimum_permission; writing never grants more connection privileges.",
)
async def skill_write(
    name: Label,
    description: Text,
    when_to_use: Text,
    instructions: Text,
    scope: Literal["workspace", "personal"],
    ctx: Context,
    version: Annotated[int, Field(ge=1)] = 1,
    tools_required: list[dict] | None = None,
) -> dict:
    return await _guarded(
        ctx,
        "skills:write",
        "skill_write",
        lambda c: _skill_service(c).write(
            name,
            description,
            when_to_use,
            instructions,
            scope=scope,
            version=version,
            tools_required=tools_required or [],
        ),
    )


@_tool(
    name="skill_attach",
    annotations=WRITES,
    description="Attach an exact skill version to this atom, only within active connection ceilings and allowed tool slugs. Does not activate a candidate atom version.",
)
async def skill_attach(
    skill_id: uuid.UUID,
    skill_version: Annotated[int, Field(ge=1)],
    ctx: Context,
    catalog: bool = False,
) -> dict:
    def attach(c):
        if not c.atom_id:
            raise PermissionError("This tool requires a run-scoped atom token")
        return _skill_service(c).attach(str(skill_id), skill_version=skill_version, catalog=catalog)

    return await _guarded(ctx, "skills:write", "skill_attach", attach)


@_tool(
    name="aggregate_read",
    annotations=READ_ONLY,
    description="Return counts, safe groupings or time trends permitted by live summary/read/write grants. Never returns raw row IDs, titles or bodies.",
)
async def aggregate_read(
    resource_type: Literal[
        "project", "team", "collection", "channel", "folder", "memory", "meeting", "connection"
    ],
    ctx: Context,
    resource_id: uuid.UUID | None = None,
    group_by: str | None = None,
    trend: str | None = None,
) -> dict:
    from app.domain.atoms.aggregate import AggregateService

    return await _guarded(
        ctx,
        "aggregate:read",
        "aggregate_read",
        lambda c: AggregateService(
            c.workspace_id,
            actor=c.member_id,
            atom_id=c.atom_id,
            run_id=c.run_id,
        ).read(
            resource_type,
            resource_id=str(resource_id) if resource_id else None,
            group_by=group_by,
            trend=trend,
        ),
    )


@_tool(
    name="atom_approval_create",
    annotations=WRITES,
    description="Request human approval for a tool action in the current atom run. Reuse request_key on retries. Approval does not authorize execution: the trusted backend must bootstrap an execution run, which must win the one-time REST claim before attempting the external effect.",
)
async def atom_approval_create(
    title: Label,
    payload: dict,
    ctx: Context,
    request_key: Label | None = None,
    kind: Literal["plan", "pull_request", "deploy", "action", "atom_action"] = "atom_action",
    assigned_to: uuid.UUID | None = None,
    expires_at: datetime | None = None,
) -> dict:
    from app.domain.atoms.approvals import ApprovalService

    return await _guarded(
        ctx,
        "atoms:run",
        "atom_approval_create",
        lambda c: ApprovalService(
            c.workspace_id,
            actor=c.member_id,
            atom_id=c.atom_id,
            run_id=c.run_id,
        ).create_approval(
            kind=kind,
            title=title,
            payload=payload,
            request_key=request_key,
            assigned_to=assigned_to,
            expires_at=expires_at,
        ),
    )


def _approval_service(claims: Claims):
    from app.domain.atoms.approvals import ApprovalService

    return ApprovalService(
        claims.workspace_id,
        actor=claims.member_id,
        atom_id=claims.atom_id,
        run_id=claims.run_id,
    )


@_tool(
    name="atom_approval_get",
    annotations=READ_ONLY,
    description="Read an approval only from its originating or dedicated execution run, including terminal-run retries.",
)
async def atom_approval_get(approval_id: uuid.UUID, ctx: Context) -> dict:
    return await _guarded(
        ctx,
        "atoms:run",
        "atom_approval_get",
        lambda c: _approval_service(c).get_approval(str(approval_id)),
    )


@_tool(
    name="atom_approval_claim",
    annotations=WRITES,
    description="Consume the one-time permission to attempt the approved external effect. Only execute=true from this call authorizes execution. A retry always returns execute=false; a lost response must never lead to a retry of the external effect. Requires the dedicated execution-run token.",
)
async def atom_approval_claim(approval_id: uuid.UUID, ctx: Context) -> dict:
    return await _guarded(
        ctx,
        "atoms:run",
        "atom_approval_claim",
        lambda c: _approval_service(c).claim(str(approval_id)),
    )


@_tool(
    name="atom_approval_outcome",
    annotations=WRITES,
    description="Record succeeded, failed or unknown after the bound run claims approval. First outcome wins; retries return stored data, including after run completion. No outcome requeues or grants another attempt.",
)
async def atom_approval_outcome(
    approval_id: uuid.UUID,
    status: Literal["succeeded", "failed", "unknown"],
    ctx: Context,
    result: dict | None = None,
) -> dict:
    return await _guarded(
        ctx,
        "atoms:run",
        "atom_approval_outcome",
        lambda c: _approval_service(c).outcome(str(approval_id), status=status, result=result),
    )
