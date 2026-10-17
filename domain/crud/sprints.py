from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.enums import SprintStatus
from db.models import ProjectTeam, Sprint
from domain.crud import apply_update, delete, fetch_page, get_or_404, save
from domain.crud.projects import get_project
from domain.errors import not_found
from schemas.sprint import SprintCreate, SprintUpdate


def create_sprint(
    session: Session, org_id: UUID, project_id: UUID, team_id: UUID, data: SprintCreate
) -> Sprint:
    pair = session.scalar(
        select(ProjectTeam).where(
            ProjectTeam.project_id == project_id,
            ProjectTeam.team_id == team_id,
            ProjectTeam.organization_id == org_id,
        )
    )
    if pair is None:
        raise not_found("PROJECT_TEAM")
    return save(
        session,
        Sprint(
            organization_id=pair.organization_id,
            project_id=pair.project_id,
            team_id=pair.team_id,
            **data.model_dump(),
        ),
    )


def list_sprints(
    session: Session,
    org_id: UUID,
    project_id: UUID,
    team_id: UUID | None,
    status: SprintStatus | None,
    limit: int,
    offset: int,
):
    get_project(session, org_id, project_id)
    stmt = select(Sprint).where(
        Sprint.project_id == project_id, Sprint.organization_id == org_id
    )
    if team_id is not None:
        stmt = stmt.where(Sprint.team_id == team_id)
    if status is not None:
        stmt = stmt.where(Sprint.status == status)
    return fetch_page(session, stmt.order_by(Sprint.created_at, Sprint.id), limit, offset)


def get_sprint(session: Session, org_id: UUID, sprint_id: UUID) -> Sprint:
    return get_or_404(session, Sprint, org_id, sprint_id, "SPRINT")


def update_sprint(
    session: Session, org_id: UUID, sprint_id: UUID, data: SprintUpdate
) -> Sprint:
    return apply_update(session, get_sprint(session, org_id, sprint_id), data)


def delete_sprint(session: Session, org_id: UUID, sprint_id: UUID) -> None:
    """Hard delete; the DB sets tasks.sprint_id to NULL (back to the backlog)."""
    delete(session, get_sprint(session, org_id, sprint_id))
