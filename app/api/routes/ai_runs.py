"""AI runs: paused agent runs and their review decision (service key only)."""

from uuid import UUID

from fastapi import APIRouter, Depends, Response

from app.api.auth import service_only
from app.api.errors import api_error
from app.api.schemas.ai_runs import AiRunIn, AiRunOut, ClaimIn, ReviewIn
from app.db.session import service_session
from app.domain.identity import ai_runs as store
from app.domain.identity.ai_runs import AiRunError

router = APIRouter(prefix="/ai-runs", dependencies=[Depends(service_only)])


def _run(fn, *args):
    try:
        with service_session() as session:
            return fn(session, *args)
    except AiRunError as exc:
        raise api_error(exc.status, exc.code, str(exc)) from None


def _read(session, task_id: UUID) -> AiRunOut:
    return AiRunOut.model_validate(store.get_run(session, task_id), from_attributes=True)


@router.put("/{task_id}", status_code=204)
def put_run(task_id: UUID, body: AiRunIn):
    _run(
        store.put_run,
        task_id,
        body.workspace_id,
        body.project_id,
        body.agent_member_id,
        body.requester_member_id,
        body.stage,
        body.state,
    )
    return Response(status_code=204)


@router.get("/{task_id}", response_model=AiRunOut)
def get_run(task_id: UUID):
    return _run(_read, task_id)


def _claim(session, task_id: UUID, expected_stage: str) -> AiRunOut:
    return AiRunOut.model_validate(
        store.claim_run(session, task_id, expected_stage), from_attributes=True
    )


@router.post("/{task_id}/claim", response_model=AiRunOut)
def claim_run(task_id: UUID, body: ClaimIn):
    return _run(_claim, task_id, body.expected_stage)


@router.patch("/{task_id}/review", status_code=204)
def set_review(task_id: UUID, body: ReviewIn):
    _run(store.set_review, task_id, body.status, body.reason)
    return Response(status_code=204)


@router.delete("/{task_id}", status_code=204)
def delete_run(task_id: UUID):
    _run(store.delete_run, task_id)
    return Response(status_code=204)
