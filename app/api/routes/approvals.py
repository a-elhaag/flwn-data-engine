"""Approval requests are run-bound; humans decide through the trusted backend."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request

from app.api.approval_schemas import (
    ApprovalBootstrap,
    ApprovalDecision,
    ApprovalOutcome,
    CreateApproval,
)
from app.api.auth import WorkspaceId, allow
from app.api.routes.atoms import _call
from app.atoms.approvals import ApprovalService

router = APIRouter(prefix="/workspaces/{workspace_id}/approvals")


def _bound(request, workspace_id):
    p = request.state.principal
    return ApprovalService(str(workspace_id), actor=p.member_id, atom_id=p.atom_id, run_id=p.run_id)


@router.post("", status_code=201, dependencies=[allow("atoms:run")])
def create_approval(workspace_id: WorkspaceId, body: CreateApproval, request: Request):
    return _call(_bound(request, workspace_id).create_approval, **body.model_dump(mode="json"))


@router.get("/ready", dependencies=[allow(None)])
def ready_approvals(workspace_id: WorkspaceId, limit: Annotated[int, Query(ge=1, le=200)] = 100):
    return _call(ApprovalService(str(workspace_id), trusted=True).ready, limit=limit)


@router.get("/{approval_id}", dependencies=[allow("atoms:run", allow_finished=True)])
def get_approval(workspace_id: WorkspaceId, approval_id: UUID, request: Request):
    return _call(_bound(request, workspace_id).get_approval, str(approval_id))


@router.put("/{approval_id}/decision", dependencies=[allow(None)])
def decide_approval(
    workspace_id: WorkspaceId,
    approval_id: UUID,
    body: ApprovalDecision,
    request: Request,
):
    service = ApprovalService(
        str(workspace_id),
        actor=request.state.principal.member_id,
        trusted=True,
    )
    return _call(service.decide, str(approval_id), **body.model_dump())


@router.post("/{approval_id}/execution-run", dependencies=[allow(None)])
def bootstrap_approval(workspace_id: WorkspaceId, approval_id: UUID, body: ApprovalBootstrap):
    return _call(
        ApprovalService(str(workspace_id), trusted=True).bootstrap,
        str(approval_id),
        **body.model_dump(),
    )


@router.post("/{approval_id}/claim", dependencies=[allow("atoms:run", allow_finished=True)])
def claim_approval(workspace_id: WorkspaceId, approval_id: UUID, request: Request):
    return _call(_bound(request, workspace_id).claim, str(approval_id))


@router.put("/{approval_id}/outcome", dependencies=[allow("atoms:run", allow_finished=True)])
def approval_outcome(
    workspace_id: WorkspaceId,
    approval_id: UUID,
    body: ApprovalOutcome,
    request: Request,
):
    return _call(_bound(request, workspace_id).outcome, str(approval_id), **body.model_dump())
