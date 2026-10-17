from uuid import UUID

from fastapi import APIRouter, Response

from domain.crud import workspaces as ws_crud
from domain.deps import OrgId, PageParams, PathOrg, ServiceKey, SessionDep
from domain.errors import DomainRoute, error_responses
from domain.pagination import Page, make_page
from schemas.workspace import WorkspaceCreate, WorkspaceRead, WorkspaceUpdate

router = APIRouter(
    tags=["workspaces"], route_class=DomainRoute, dependencies=[ServiceKey]
)


@router.post(
    "/organizations/{orgId}/workspaces",
    status_code=201,
    response_model=WorkspaceRead,
    summary="Create a workspace (department)",
    responses=error_responses(404, 409, 422),
)
def create_workspace(org: PathOrg, body: WorkspaceCreate, session: SessionDep):
    return ws_crud.create_workspace(session, org.id, body)


@router.get(
    "/organizations/{orgId}/workspaces",
    response_model=Page[WorkspaceRead],
    summary="List workspaces of an organization",
    description="Archived rows are included unless `archived` is set.",
    responses=error_responses(404, 422),
)
def list_workspaces(
    org: PathOrg, session: SessionDep, pg: PageParams, archived: bool | None = None
):
    rows, more = ws_crud.list_workspaces(session, org.id, archived, pg.limit, pg.offset)
    return make_page(rows, more, pg.limit, pg.offset)


@router.get(
    "/workspaces/{workspaceId}",
    response_model=WorkspaceRead,
    summary="Get a workspace",
    responses=error_responses(400, 404, 422),
)
def get_workspace(workspaceId: UUID, org_id: OrgId, session: SessionDep):  # noqa: N803
    return ws_crud.get_workspace(session, org_id, workspaceId)


@router.patch(
    "/workspaces/{workspaceId}",
    response_model=WorkspaceRead,
    summary="Update a workspace",
    description="`archived: true` sets archivedAt, `false` clears it.",
    responses=error_responses(400, 404, 409, 422),
)
def update_workspace(
    workspaceId: UUID, body: WorkspaceUpdate, org_id: OrgId, session: SessionDep  # noqa: N803
):
    return ws_crud.update_workspace(session, org_id, workspaceId, body)


@router.delete(
    "/workspaces/{workspaceId}",
    status_code=204,
    summary="Archive a workspace",
    description="Archives (sets archivedAt); it does not remove the row.",
    responses=error_responses(400, 404, 422),
)
def archive_workspace(workspaceId: UUID, org_id: OrgId, session: SessionDep):  # noqa: N803
    ws_crud.archive_workspace(session, org_id, workspaceId)
    return Response(status_code=204)
