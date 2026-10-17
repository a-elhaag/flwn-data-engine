from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.enums import ProjectStatus
from db.models import Project, ProjectTeam
from domain.crud import apply_update, archive, fetch_page, get_or_404, save
from domain.crud.workspaces import get_workspace
from domain.errors import not_found
from schemas.project import ProjectCreate, ProjectTeamCreate, ProjectUpdate


def create_project(
    session: Session, org_id: UUID, workspace_id: UUID, data: ProjectCreate
) -> Project:
    workspace = get_workspace(session, org_id, workspace_id)
    return save(
        session,
        Project(
            organization_id=workspace.organization_id,
            workspace_id=workspace.id,
            **data.model_dump(),
        ),
    )


def list_projects(
    session: Session,
    org_id: UUID,
    workspace_id: UUID,
    archived: bool | None,
    status: ProjectStatus | None,
    limit: int,
    offset: int,
):
    get_workspace(session, org_id, workspace_id)
    stmt = select(Project).where(
        Project.workspace_id == workspace_id, Project.organization_id == org_id
    )
    if archived is not None:
        stmt = stmt.where(Project.archived_at.is_not(None) == archived)
    if status is not None:
        stmt = stmt.where(Project.status == status)
    return fetch_page(session, stmt.order_by(Project.created_at, Project.id), limit, offset)


def get_project(session: Session, org_id: UUID, project_id: UUID) -> Project:
    return get_or_404(session, Project, org_id, project_id, "PROJECT")


def update_project(
    session: Session, org_id: UUID, project_id: UUID, data: ProjectUpdate
) -> Project:
    return apply_update(session, get_project(session, org_id, project_id), data)


def archive_project(session: Session, org_id: UUID, project_id: UUID) -> None:
    archive(session, get_project(session, org_id, project_id))


def add_project_team(
    session: Session, org_id: UUID, project_id: UUID, data: ProjectTeamCreate
) -> ProjectTeam:
    project = get_project(session, org_id, project_id)
    return save(
        session,
        ProjectTeam(
            project_id=project.id, organization_id=project.organization_id, **data.model_dump()
        ),
    )


def list_project_teams(
    session: Session, org_id: UUID, project_id: UUID, limit: int, offset: int
):
    get_project(session, org_id, project_id)
    stmt = (
        select(ProjectTeam)
        .where(ProjectTeam.project_id == project_id, ProjectTeam.organization_id == org_id)
        .order_by(ProjectTeam.joined_at, ProjectTeam.team_id)
    )
    return fetch_page(session, stmt, limit, offset)


def remove_project_team(
    session: Session, org_id: UUID, project_id: UUID, team_id: UUID
) -> None:
    """Hard delete. The DB refuses while the team still has sprints in the project
    (mapped to PROJECT_TEAM_HAS_SPRINTS)."""
    get_project(session, org_id, project_id)
    row = session.scalar(
        select(ProjectTeam).where(
            ProjectTeam.project_id == project_id,
            ProjectTeam.team_id == team_id,
            ProjectTeam.organization_id == org_id,
        )
    )
    if row is None:
        raise not_found("PROJECT_TEAM")
    session.delete(row)
    session.commit()
