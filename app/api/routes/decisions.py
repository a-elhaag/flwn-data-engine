"""Decision Ledger: record decisions, check proposed work against them, handle flagged conflicts."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from app.api import tokens
from app.api.auth import Principal
from app.api.deps import DECISIONS_READ, DECISIONS_WRITE, Decisions
from app.api.schemas import (
    CheckDecisionRequest,
    DecisionStatus,
    RecordDecisionRequest,
    ResolveConflictRequest,
    SupersedeDecisionRequest,
    UpdateDecisionRequest,
)
from app.decisions.service import (
    ConflictPage,
    ConflictView,
    DecisionPage,
    DecisionView,
    RecordResult,
    Verdict,
)

router = APIRouter(prefix="/workspaces/{workspace_id}/decisions")


@router.post("", status_code=201)
def record(
    _: Annotated[Principal, DECISIONS_WRITE], request: RecordDecisionRequest, ledger: Decisions
) -> RecordResult:
    """Record a decision. A near-identical active decision is returned (`deduplicated`)."""
    data = request.model_dump()
    data["scope_paths"] = data.pop("files_scope")
    return ledger.record(**data)


@router.post("/check")
def check(
    principal: Annotated[Principal, DECISIONS_READ],
    request: CheckDecisionRequest,
    ledger: Decisions,
) -> Verdict:
    """Does the proposal go against an active decision? Writes nothing unless `record` is true,
    which needs `decisions:write`."""
    if (
        request.record
        and not principal.service
        and tokens.SCOPE_DECISIONS_WRITE not in principal.scopes
    ):
        raise HTTPException(status_code=403, detail="record=true needs the decisions:write scope")
    data = request.model_dump(exclude={"agent"})
    return ledger.check(data.pop("proposed_action"), **data)


@router.get("/conflicts", dependencies=[DECISIONS_READ])
def list_conflicts(
    ledger: Decisions,
    status: Annotated[str | None, Query(pattern="^(open|accepted|dismissed|resolved)$")] = None,
    decision_id: UUID | None = None,
    task_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ConflictPage:
    return ledger.conflicts(
        status, str(decision_id) if decision_id else None, task_id, limit, offset
    )


@router.post("/conflicts/{conflict_id}/resolve", dependencies=[DECISIONS_WRITE])
def resolve_conflict(
    conflict_id: UUID, request: ResolveConflictRequest, ledger: Decisions
) -> ConflictView:
    """Human members only: an agent can flag a conflict but never close it."""
    return ledger.resolve(str(conflict_id), request.status, request.note)


@router.get("", dependencies=[DECISIONS_READ])
def list_decisions(
    ledger: Decisions,
    status: DecisionStatus | None = None,
    area: str | None = None,
    team_id: UUID | None = None,
    project_id: UUID | None = None,
    q: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DecisionPage:
    return ledger.list(status, area, team_id, project_id, q, limit, offset)


@router.get("/{decision_id}", dependencies=[DECISIONS_READ])
def get_decision(decision_id: UUID, ledger: Decisions) -> DecisionView:
    return ledger.get(str(decision_id))


@router.patch("/{decision_id}", dependencies=[DECISIONS_WRITE])
def update_decision(
    decision_id: UUID, request: UpdateDecisionRequest, ledger: Decisions
) -> DecisionView:
    data = request.model_dump(exclude_unset=True)
    if "files_scope" in data:
        data["scope_paths"] = data.pop("files_scope")
    return ledger.update(str(decision_id), **data)


@router.post("/{decision_id}/supersede", status_code=201, dependencies=[DECISIONS_WRITE])
def supersede_decision(
    decision_id: UUID, request: SupersedeDecisionRequest, ledger: Decisions
) -> RecordResult:
    data = request.model_dump()
    data["scope_paths"] = data.pop("files_scope")
    return ledger.supersede(str(decision_id), **data)
