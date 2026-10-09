"""Decision conflicts flagged by the AI engine's ledger. Agents flag; only human members resolve."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import ADMIN, CONFLICTS_READ, CONFLICTS_WRITE, Conflicts
from app.api.schemas import FlagConflictRequest, ResolveConflictRequest
from app.decisions.service import ConflictPage, ConflictView

router = APIRouter(prefix="/workspaces/{workspace_id}/decision-conflicts")


@router.post("", status_code=201, dependencies=[CONFLICTS_WRITE])
def flag(request: FlagConflictRequest, conflicts: Conflicts) -> ConflictView:
    """Flag a proposal that contradicts a decision. An identical open flag is reused."""
    data = request.model_dump()
    data["decision_id"] = str(request.decision_id)
    return conflicts.flag(**data)


@router.get("", dependencies=[CONFLICTS_READ])
def list_conflicts(
    conflicts: Conflicts,
    status: Annotated[str | None, Query(pattern="^(open|accepted|dismissed|resolved)$")] = None,
    decision_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ConflictPage:
    return conflicts.list(status, str(decision_id) if decision_id else None, limit, offset)


@router.get("/{conflict_id}", dependencies=[CONFLICTS_READ])
def get_conflict(conflict_id: UUID, conflicts: Conflicts) -> ConflictView:
    return conflicts.get(str(conflict_id))


@router.put("/{conflict_id}/resolution", dependencies=[ADMIN])
def resolve(
    conflict_id: UUID, request: ResolveConflictRequest, conflicts: Conflicts
) -> ConflictView:
    """Trusted backend only, acting as a human member (X-Acting-Member-Id). Agent tokens are
    refused, so an agent can never close its own flag."""
    return conflicts.resolve(str(conflict_id), request.status, request.note)
