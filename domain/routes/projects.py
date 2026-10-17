from uuid import UUID

from fastapi import APIRouter, Response

from db.enums import ProjectStatus
from domain.crud import projects as projects_crud
from domain.deps import OrgId, PageParams, ServiceKey, SessionDep
from domain.errors import DomainRoute, error_responses
from domain.pagination import Page, make_page
from schemas.project import (
    ProjectCreate,
    ProjectRead,
    ProjectTeamCreate,
    ProjectTeamRead,
    ProjectUpdate,
)

router = APIRouter(
    tags=["projects"], route_class=DomainRoute, dependencies=[ServiceKey]
)


@router.post(
    "/workspaces/{workspaceId}/projects",
    status_code=201,
    response_model=ProjectRead,
    summary="Create a project",
    description="The key (e.g. FLWN) is permanent and unique per organization, archived projects included.",
    responses=error_responses(400, 404, 409, 422),
)
def create_project(
    workspaceId: UUID, body: ProjectCreate, org_id: OrgId, session: SessionDep  # noqa: N803
):
    return projects_crud.create_project(session, org_id, workspaceId, body)


@router.get(
    "/workspaces/{workspaceId}/projects",
    response_model=Page[ProjectRead],
    summary="List projects of a workspace",
    description="Archived rows are included unless `archived` is set.",
    responses=error_responses(400, 404, 422),
)
def list_projects(
    workspaceId: UUID,  # noqa: N803
    org_id: OrgId,
    session: SessionDep,
    pg: PageParams,
    archived: bool | None = None,
    status: ProjectStatus | None = None,
):
    rows, more = projects_crud.list_projects(
        session, org_id, workspaceId, archived, status, pg.limit, pg.offset
    )
    return make_page(rows, more, pg.limit, pg.offset)


@router.get(
    "/projects/{projectId}",
    response_model=ProjectRead,
    summary="Get a project",
    responses=error_responses(400, 404, 422),
)
def get_project(projectId: UUID, org_id: OrgId, session: SessionDep):  # noqa: N803
    return projects_crud.get_project(session, org_id, projectId)


@router.patch(
    "/projects/{projectId}",
    response_model=ProjectRead,
    summary="Update a project",
    description="`archived: true` sets archivedAt, `false` clears it. The key never changes.",
    responses=error_responses(400, 404, 409, 422),
)
def update_project(
    projectId: UUID, body: ProjectUpdate, org_id: OrgId, session: SessionDep  # noqa: N803
):
    return projects_crud.update_project(session, org_id, projectId, body)


@router.delete(
    "/projects/{projectId}",
    status_code=204,
    summary="Archive a project",
    description="Archives (sets archivedAt); it does not remove the row.",
    responses=error_responses(400, 404, 422),
)
def archive_project(projectId: UUID, org_id: OrgId, session: SessionDep):  # noqa: N803
    projects_crud.archive_project(session, org_id, projectId)
    return Response(status_code=204)


@router.post(
    "/projects/{projectId}/teams",
    status_code=201,
    response_model=ProjectTeamRead,
    summary="Add a team to a project",
    description="The team may belong to any workspace of the same organization.",
    responses=error_responses(400, 404, 409, 422),
)
def add_project_team(
    projectId: UUID, body: ProjectTeamCreate, org_id: OrgId, session: SessionDep  # noqa: N803
):
    return projects_crud.add_project_team(session, org_id, projectId, body)


@router.get(
    "/projects/{projectId}/teams",
    response_model=Page[ProjectTeamRead],
    summary="List teams on a project",
    responses=error_responses(400, 404, 422),
)
def list_project_teams(
    projectId: UUID, org_id: OrgId, session: SessionDep, pg: PageParams  # noqa: N803
):
    rows, more = projects_crud.list_project_teams(
        session, org_id, projectId, pg.limit, pg.offset
    )
    return make_page(rows, more, pg.limit, pg.offset)


@router.delete(
    "/projects/{projectId}/teams/{teamId}",
    status_code=204,
    summary="Remove a team from a project",
    description="Hard delete. Returns 409 PROJECT_TEAM_HAS_SPRINTS while the team still has sprints in the project.",
    responses=error_responses(400, 404, 409, 422),
)
def remove_project_team(
    projectId: UUID, teamId: UUID, org_id: OrgId, session: SessionDep  # noqa: N803
):
    projects_crud.remove_project_team(session, org_id, projectId, teamId)
    return Response(status_code=204)
