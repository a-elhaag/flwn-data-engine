from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Project, ProjectTeam, Team, TeamMember
from domain.crud import apply_update, archive, fetch_page, get_or_404, save
from domain.crud.workspaces import get_workspace
from domain.errors import not_found
from schemas.team import TeamCreate, TeamMemberCreate, TeamMemberUpdate, TeamUpdate


def create_team(
    session: Session, org_id: UUID, workspace_id: UUID, data: TeamCreate
) -> Team:
    workspace = get_workspace(session, org_id, workspace_id)
    return save(
        session,
        Team(
            organization_id=workspace.organization_id,
            workspace_id=workspace.id,
            **data.model_dump(),
        ),
    )


def list_teams(
    session: Session,
    org_id: UUID,
    workspace_id: UUID,
    archived: bool | None,
    limit: int,
    offset: int,
):
    get_workspace(session, org_id, workspace_id)
    stmt = select(Team).where(
        Team.workspace_id == workspace_id, Team.organization_id == org_id
    )
    if archived is not None:
        stmt = stmt.where(Team.archived_at.is_not(None) == archived)
    return fetch_page(session, stmt.order_by(Team.created_at, Team.id), limit, offset)


def get_team(session: Session, org_id: UUID, team_id: UUID) -> Team:
    return get_or_404(session, Team, org_id, team_id, "TEAM")


def update_team(session: Session, org_id: UUID, team_id: UUID, data: TeamUpdate) -> Team:
    return apply_update(session, get_team(session, org_id, team_id), data)


def archive_team(session: Session, org_id: UUID, team_id: UUID) -> None:
    archive(session, get_team(session, org_id, team_id))


def add_team_member(
    session: Session, org_id: UUID, team_id: UUID, data: TeamMemberCreate
) -> TeamMember:
    team = get_team(session, org_id, team_id)
    return save(
        session,
        TeamMember(team_id=team.id, organization_id=team.organization_id, **data.model_dump()),
    )


def list_team_members(
    session: Session, org_id: UUID, team_id: UUID, limit: int, offset: int
):
    get_team(session, org_id, team_id)
    stmt = (
        select(TeamMember)
        .where(TeamMember.team_id == team_id, TeamMember.organization_id == org_id)
        .order_by(TeamMember.joined_at, TeamMember.member_id)
    )
    return fetch_page(session, stmt, limit, offset)


def _get_team_member(
    session: Session, org_id: UUID, team_id: UUID, member_id: UUID
) -> TeamMember:
    get_team(session, org_id, team_id)
    row = session.scalar(
        select(TeamMember).where(
            TeamMember.team_id == team_id,
            TeamMember.member_id == member_id,
            TeamMember.organization_id == org_id,
        )
    )
    if row is None:
        raise not_found("TEAM_MEMBER")
    return row


def update_team_member(
    session: Session, org_id: UUID, team_id: UUID, member_id: UUID, data: TeamMemberUpdate
) -> TeamMember:
    return apply_update(
        session, _get_team_member(session, org_id, team_id, member_id), data
    )


def remove_team_member(
    session: Session, org_id: UUID, team_id: UUID, member_id: UUID
) -> None:
    session.delete(_get_team_member(session, org_id, team_id, member_id))
    session.commit()


def list_team_projects(
    session: Session, org_id: UUID, team_id: UUID, limit: int, offset: int
):
    get_team(session, org_id, team_id)
    project_ids = select(ProjectTeam.project_id).where(
        ProjectTeam.team_id == team_id, ProjectTeam.organization_id == org_id
    )
    stmt = (
        select(Project)
        .where(Project.id.in_(project_ids), Project.organization_id == org_id)
        .order_by(Project.created_at, Project.id)
    )
    return fetch_page(session, stmt, limit, offset)
