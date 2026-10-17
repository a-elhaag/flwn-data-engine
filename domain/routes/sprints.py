from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from db.enums import SprintStatus
from domain.crud import sprints as sprints_crud
from domain.deps import OrgId, PageParams, ServiceKey, SessionDep
from domain.errors import DomainRoute, error_responses
from domain.pagination import Page, make_page
from schemas.sprint import SprintCreate, SprintRead, SprintUpdate

router = APIRouter(
    tags=["sprints"], route_class=DomainRoute, dependencies=[ServiceKey]
)


@router.post(
    "/projects/{projectId}/teams/{teamId}/sprints",
    status_code=201,
    response_model=SprintRead,
    summary="Create a sprint for a team in a project",
    description="The team must already be on the project, otherwise 404 PROJECT_TEAM_NOT_FOUND.",
    responses=error_responses(400, 404, 409, 422),
)
def create_sprint(
    projectId: UUID,  # noqa: N803
    teamId: UUID,  # noqa: N803
    body: SprintCreate,
    org_id: OrgId,
    session: SessionDep,
):
    return sprints_crud.create_sprint(session, org_id, projectId, teamId, body)


@router.get(
    "/projects/{projectId}/sprints",
    response_model=Page[SprintRead],
    summary="List sprints of a project",
    responses=error_responses(400, 404, 422),
)
def list_sprints(
    projectId: UUID,  # noqa: N803
    org_id: OrgId,
    session: SessionDep,
    pg: PageParams,
    team_id: Annotated[UUID | None, Query(alias="teamId")] = None,
    status: SprintStatus | None = None,
):
    rows, more = sprints_crud.list_sprints(
        session, org_id, projectId, team_id, status, pg.limit, pg.offset
    )
    return make_page(rows, more, pg.limit, pg.offset)


@router.get(
    "/sprints/{sprintId}",
    response_model=SprintRead,
    summary="Get a sprint",
    responses=error_responses(400, 404, 422),
)
def get_sprint(sprintId: UUID, org_id: OrgId, session: SessionDep):  # noqa: N803
    return sprints_crud.get_sprint(session, org_id, sprintId)


@router.patch(
    "/sprints/{sprintId}",
    response_model=SprintRead,
    summary="Update a sprint",
    responses=error_responses(400, 404, 409, 422),
)
def update_sprint(
    sprintId: UUID, body: SprintUpdate, org_id: OrgId, session: SessionDep  # noqa: N803
):
    return sprints_crud.update_sprint(session, org_id, sprintId, body)


@router.delete(
    "/sprints/{sprintId}",
    status_code=204,
    summary="Delete a sprint",
    description="Hard delete. Its tasks are kept and go back to the backlog (sprintId = null).",
    responses=error_responses(400, 404, 422),
)
def delete_sprint(sprintId: UUID, org_id: OrgId, session: SessionDep):  # noqa: N803
    sprints_crud.delete_sprint(session, org_id, sprintId)
    return Response(status_code=204)
