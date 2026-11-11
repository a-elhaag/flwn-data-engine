"""AI runs: a paused agent run (plan or human review) and the review decision on it. Service key only.

Plain functions over a session; the route opens it with service_session(). The AI engine owns the
graph state (stored as given); the backend records the review decision.
"""

import uuid

from sqlalchemy import delete, func, update
from sqlalchemy.dialects.postgresql import insert

from app.db.models.auth import AiRun
from app.domain.error_codes import DomainError


class AiRunError(DomainError):
    """A client error with the HTTP status the route answers with."""

    status = 404


class AiRunNotFound(AiRunError, LookupError):
    code = "ai_run_not_found"


class AiRunNotInReview(AiRunError):
    code = "ai_run_not_in_review"
    status = 409


class AiRunNotClaimable(AiRunError):
    code = "ai_run_not_claimable"
    status = 409


def put_run(
    session,
    task_id: uuid.UUID,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    agent_member_id: uuid.UUID,
    requester_member_id: uuid.UUID,
    stage: str,
    state: dict,
) -> None:
    fields = {
        "workspace_id": workspace_id,
        "project_id": project_id,
        "agent_member_id": agent_member_id,
        "requester_member_id": requester_member_id,
        "stage": stage,
        "state": state,
    }
    stmt = insert(AiRun).values(
        task_id=task_id, review_status="pending", review_reason=None, **fields
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=[AiRun.task_id],
            set_={
                **{key: getattr(stmt.excluded, key) for key in fields},
                "review_status": "pending",
                "review_reason": None,
            },
        )
    )


def get_run(session, task_id: uuid.UUID) -> AiRun:
    run = session.get(AiRun, task_id)
    if run is None:
        raise AiRunNotFound("AI run not found")
    return run


def set_review(session, task_id: uuid.UUID, status: str, reason: str | None) -> None:
    run = get_run(session, task_id)
    if run.stage != "review":
        raise AiRunNotInReview("AI run is not waiting for review")
    run.review_status = status
    run.review_reason = reason


def delete_run(session, task_id: uuid.UUID) -> None:
    session.execute(delete(AiRun).where(AiRun.task_id == task_id))


def claim_run(session, task_id: uuid.UUID, expected_stage: str) -> AiRun:
    """Move a run to 'resuming' if it is at expected_stage. One statement, so a redelivered
    decision can claim a run only once."""
    run = session.scalars(
        update(AiRun)
        .where(AiRun.task_id == task_id, AiRun.stage == expected_stage)
        .values(stage="resuming", updated_at=func.now())
        .returning(AiRun)
    ).first()
    if run is not None:
        return run
    get_run(session, task_id)  # 404 if the run does not exist
    raise AiRunNotClaimable("AI run is not at the expected stage")
