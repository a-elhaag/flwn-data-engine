from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from db.enums import MemberRole, MemberStatus, MemberType, TaskStatus
from domain.crud import members as members_crud
from domain.deps import OrgId, PageParams, PathOrg, ServiceKey, SessionDep
from domain.errors import DomainRoute, error_responses
from domain.pagination import Page, make_page
from schemas.member import MemberCreate, MemberRead, MemberUpdate
from schemas.project import ProjectRead
from schemas.task import TaskRead
from schemas.team import TeamMemberRead

router = APIRouter(
    tags=["members"], route_class=DomainRoute, dependencies=[ServiceKey]
)


@router.post(
    "/organizations/{orgId}/members",
    status_code=201,
    response_model=MemberRead,
    summary="Create a member",
    description="HUMAN needs userId (no name/email); AI needs name (no userId).",
    responses=error_responses(404, 409, 422),
)
def create_member(org: PathOrg, body: MemberCreate, session: SessionDep):
    return members_crud.create_member(session, org.id, body)


@router.get(
    "/organizations/{orgId}/members",
    response_model=Page[MemberRead],
    summary="List members of an organization",
    responses=error_responses(404, 422),
)
def list_members(
    org: PathOrg,
    session: SessionDep,
    pg: PageParams,
    type: MemberType | None = None,
    status: MemberStatus | None = None,
    role: MemberRole | None = None,
    workspace_id: Annotated[UUID | None, Query(alias="workspaceId")] = None,
):
    rows, more = members_crud.list_members(
        session, org.id, type, status, role, workspace_id, pg.limit, pg.offset
    )
    return make_page(rows, more, pg.limit, pg.offset)


@router.get(
    "/members/{memberId}",
    response_model=MemberRead,
    summary="Get a member",
    responses=error_responses(400, 404, 422),
)
def get_member(memberId: UUID, org_id: OrgId, session: SessionDep):  # noqa: N803
    return members_crud.get_member(session, org_id, memberId)


@router.patch(
    "/members/{memberId}",
    response_model=MemberRead,
    summary="Update a member",
    responses=error_responses(400, 404, 409, 422),
)
def update_member(
    memberId: UUID, body: MemberUpdate, org_id: OrgId, session: SessionDep  # noqa: N803
):
    return members_crud.update_member(session, org_id, memberId, body)


@router.get(
    "/members/{memberId}/teams",
    response_model=Page[TeamMemberRead],
    summary="List a member's team memberships",
    responses=error_responses(400, 404, 422),
)
def list_member_teams(
    memberId: UUID, org_id: OrgId, session: SessionDep, pg: PageParams  # noqa: N803
):
    rows, more = members_crud.list_member_teams(session, org_id, memberId, pg.limit, pg.offset)
    return make_page(rows, more, pg.limit, pg.offset)


@router.get(
    "/members/{memberId}/projects",
    response_model=Page[ProjectRead],
    summary="List projects of a member's teams",
    description="Distinct projects that any of the member's teams works on.",
    responses=error_responses(400, 404, 422),
)
def list_member_projects(
    memberId: UUID, org_id: OrgId, session: SessionDep, pg: PageParams  # noqa: N803
):
    rows, more = members_crud.list_member_projects(
        session, org_id, memberId, pg.limit, pg.offset
    )
    return make_page(rows, more, pg.limit, pg.offset)


@router.get(
    "/members/{memberId}/tasks",
    response_model=Page[TaskRead],
    summary="List tasks assigned to a member",
    description="Across all projects of the organization.",
    responses=error_responses(400, 404, 422),
)
def list_member_tasks(
    memberId: UUID,  # noqa: N803
    org_id: OrgId,
    session: SessionDep,
    pg: PageParams,
    status: TaskStatus | None = None,
):
    rows, more = members_crud.list_member_tasks(
        session, org_id, memberId, status, pg.limit, pg.offset
    )
    return make_page(rows, more, pg.limit, pg.offset)
