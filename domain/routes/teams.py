from uuid import UUID

from fastapi import APIRouter, Response

from domain.crud import teams as teams_crud
from domain.deps import OrgId, PageParams, ServiceKey, SessionDep
from domain.errors import DomainRoute, error_responses
from domain.pagination import Page, make_page
from schemas.project import ProjectRead
from schemas.team import (
    TeamCreate,
    TeamMemberCreate,
    TeamMemberRead,
    TeamMemberUpdate,
    TeamRead,
    TeamUpdate,
)

router = APIRouter(
    tags=["teams"], route_class=DomainRoute, dependencies=[ServiceKey]
)


@router.post(
    "/workspaces/{workspaceId}/teams",
    status_code=201,
    response_model=TeamRead,
    summary="Create a team",
    responses=error_responses(400, 404, 409, 422),
)
def create_team(
    workspaceId: UUID, body: TeamCreate, org_id: OrgId, session: SessionDep  # noqa: N803
):
    return teams_crud.create_team(session, org_id, workspaceId, body)


@router.get(
    "/workspaces/{workspaceId}/teams",
    response_model=Page[TeamRead],
    summary="List teams of a workspace",
    description="Archived rows are included unless `archived` is set.",
    responses=error_responses(400, 404, 422),
)
def list_teams(
    workspaceId: UUID,  # noqa: N803
    org_id: OrgId,
    session: SessionDep,
    pg: PageParams,
    archived: bool | None = None,
):
    rows, more = teams_crud.list_teams(
        session, org_id, workspaceId, archived, pg.limit, pg.offset
    )
    return make_page(rows, more, pg.limit, pg.offset)


@router.get(
    "/teams/{teamId}",
    response_model=TeamRead,
    summary="Get a team",
    responses=error_responses(400, 404, 422),
)
def get_team(teamId: UUID, org_id: OrgId, session: SessionDep):  # noqa: N803
    return teams_crud.get_team(session, org_id, teamId)


@router.patch(
    "/teams/{teamId}",
    response_model=TeamRead,
    summary="Update a team",
    description="`archived: true` sets archivedAt, `false` clears it.",
    responses=error_responses(400, 404, 409, 422),
)
def update_team(
    teamId: UUID, body: TeamUpdate, org_id: OrgId, session: SessionDep  # noqa: N803
):
    return teams_crud.update_team(session, org_id, teamId, body)


@router.delete(
    "/teams/{teamId}",
    status_code=204,
    summary="Archive a team",
    description="Archives (sets archivedAt); it does not remove the row.",
    responses=error_responses(400, 404, 422),
)
def archive_team(teamId: UUID, org_id: OrgId, session: SessionDep):  # noqa: N803
    teams_crud.archive_team(session, org_id, teamId)
    return Response(status_code=204)


@router.post(
    "/teams/{teamId}/members",
    status_code=201,
    response_model=TeamMemberRead,
    summary="Add a member to a team",
    description="The member may belong to any workspace of the same organization.",
    responses=error_responses(400, 404, 409, 422),
)
def add_team_member(
    teamId: UUID, body: TeamMemberCreate, org_id: OrgId, session: SessionDep  # noqa: N803
):
    return teams_crud.add_team_member(session, org_id, teamId, body)


@router.get(
    "/teams/{teamId}/members",
    response_model=Page[TeamMemberRead],
    summary="List team members",
    responses=error_responses(400, 404, 422),
)
def list_team_members(
    teamId: UUID, org_id: OrgId, session: SessionDep, pg: PageParams  # noqa: N803
):
    rows, more = teams_crud.list_team_members(session, org_id, teamId, pg.limit, pg.offset)
    return make_page(rows, more, pg.limit, pg.offset)


@router.patch(
    "/teams/{teamId}/members/{memberId}",
    response_model=TeamMemberRead,
    summary="Update a team member's role",
    responses=error_responses(400, 404, 422),
)
def update_team_member(
    teamId: UUID,  # noqa: N803
    memberId: UUID,  # noqa: N803
    body: TeamMemberUpdate,
    org_id: OrgId,
    session: SessionDep,
):
    return teams_crud.update_team_member(session, org_id, teamId, memberId, body)


@router.delete(
    "/teams/{teamId}/members/{memberId}",
    status_code=204,
    summary="Remove a member from a team",
    description="Hard delete of the team membership (not the member).",
    responses=error_responses(400, 404, 422),
)
def remove_team_member(
    teamId: UUID, memberId: UUID, org_id: OrgId, session: SessionDep  # noqa: N803
):
    teams_crud.remove_team_member(session, org_id, teamId, memberId)
    return Response(status_code=204)


@router.get(
    "/teams/{teamId}/projects",
    response_model=Page[ProjectRead],
    summary="List projects a team works on",
    responses=error_responses(400, 404, 422),
)
def list_team_projects(
    teamId: UUID, org_id: OrgId, session: SessionDep, pg: PageParams  # noqa: N803
):
    rows, more = teams_crud.list_team_projects(session, org_id, teamId, pg.limit, pg.offset)
    return make_page(rows, more, pg.limit, pg.offset)
