from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.enums import MemberRole, MemberStatus, MemberType, TaskStatus
from db.models import Member, Project, ProjectTeam, Task, TeamMember
from domain.crud import apply_update, fetch_page, get_or_404, save
from domain.crud.tasks import fetch_tasks
from schemas.member import MemberCreate, MemberUpdate


def create_member(session: Session, org_id: UUID, data: MemberCreate) -> Member:
    return save(session, Member(organization_id=org_id, **data.model_dump()))


def list_members(
    session: Session,
    org_id: UUID,
    type: MemberType | None,
    status: MemberStatus | None,
    role: MemberRole | None,
    workspace_id: UUID | None,
    limit: int,
    offset: int,
):
    stmt = select(Member).where(Member.organization_id == org_id)
    for column, value in (
        (Member.type, type),
        (Member.status, status),
        (Member.role, role),
        (Member.workspace_id, workspace_id),
    ):
        if value is not None:
            stmt = stmt.where(column == value)
    return fetch_page(session, stmt.order_by(Member.created_at, Member.id), limit, offset)


def get_member(session: Session, org_id: UUID, member_id: UUID) -> Member:
    return get_or_404(session, Member, org_id, member_id, "MEMBER")


def update_member(
    session: Session, org_id: UUID, member_id: UUID, data: MemberUpdate
) -> Member:
    return apply_update(session, get_member(session, org_id, member_id), data)


def list_member_teams(
    session: Session, org_id: UUID, member_id: UUID, limit: int, offset: int
):
    get_member(session, org_id, member_id)
    stmt = (
        select(TeamMember)
        .where(TeamMember.member_id == member_id, TeamMember.organization_id == org_id)
        .order_by(TeamMember.joined_at, TeamMember.team_id)
    )
    return fetch_page(session, stmt, limit, offset)


def list_member_projects(
    session: Session, org_id: UUID, member_id: UUID, limit: int, offset: int
):
    """Projects of the member's teams, each project once."""
    get_member(session, org_id, member_id)
    team_ids = select(TeamMember.team_id).where(
        TeamMember.member_id == member_id, TeamMember.organization_id == org_id
    )
    project_ids = select(ProjectTeam.project_id).where(
        ProjectTeam.team_id.in_(team_ids), ProjectTeam.organization_id == org_id
    )
    stmt = (
        select(Project)
        .where(Project.id.in_(project_ids), Project.organization_id == org_id)
        .order_by(Project.created_at, Project.id)
    )
    return fetch_page(session, stmt, limit, offset)


def list_member_tasks(
    session: Session,
    org_id: UUID,
    member_id: UUID,
    status: TaskStatus | None,
    limit: int,
    offset: int,
):
    get_member(session, org_id, member_id)
    conditions = [Task.assignee_id == member_id, Task.organization_id == org_id]
    if status is not None:
        conditions.append(Task.status == status)
    return fetch_tasks(session, conditions, limit, offset)
