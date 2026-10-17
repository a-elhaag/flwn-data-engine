from uuid import UUID

from fastapi import APIRouter, Response

from db.enums import Priority, WorkItemStatus, WorkItemType
from domain.crud import work_items as wi_crud
from domain.deps import OrgId, PageParams, ServiceKey, SessionDep
from domain.errors import DomainRoute, error_responses
from domain.pagination import Page, make_page
from schemas.work_item import WorkItemCreate, WorkItemRead, WorkItemUpdate

router = APIRouter(
    tags=["work-items"], route_class=DomainRoute, dependencies=[ServiceKey]
)


@router.post(
    "/projects/{projectId}/work-items",
    status_code=201,
    response_model=WorkItemRead,
    summary="Create a work item",
    responses=error_responses(400, 404, 422),
)
def create_work_item(
    projectId: UUID, body: WorkItemCreate, org_id: OrgId, session: SessionDep  # noqa: N803
):
    return wi_crud.create_work_item(session, org_id, projectId, body)


@router.get(
    "/projects/{projectId}/work-items",
    response_model=Page[WorkItemRead],
    summary="List work items of a project",
    responses=error_responses(400, 404, 422),
)
def list_work_items(
    projectId: UUID,  # noqa: N803
    org_id: OrgId,
    session: SessionDep,
    pg: PageParams,
    status: WorkItemStatus | None = None,
    type: WorkItemType | None = None,
    priority: Priority | None = None,
):
    rows, more = wi_crud.list_work_items(
        session, org_id, projectId, status, type, priority, pg.limit, pg.offset
    )
    return make_page(rows, more, pg.limit, pg.offset)


@router.get(
    "/work-items/{workItemId}",
    response_model=WorkItemRead,
    summary="Get a work item",
    responses=error_responses(400, 404, 422),
)
def get_work_item(workItemId: UUID, org_id: OrgId, session: SessionDep):  # noqa: N803
    return wi_crud.get_work_item(session, org_id, workItemId)


@router.patch(
    "/work-items/{workItemId}",
    response_model=WorkItemRead,
    summary="Update a work item",
    responses=error_responses(400, 404, 422),
)
def update_work_item(
    workItemId: UUID, body: WorkItemUpdate, org_id: OrgId, session: SessionDep  # noqa: N803
):
    return wi_crud.update_work_item(session, org_id, workItemId, body)


@router.delete(
    "/work-items/{workItemId}",
    status_code=204,
    summary="Delete a work item",
    description="Hard delete. Its tasks are kept with workItemId = null.",
    responses=error_responses(400, 404, 422),
)
def delete_work_item(workItemId: UUID, org_id: OrgId, session: SessionDep):  # noqa: N803
    wi_crud.delete_work_item(session, org_id, workItemId)
    return Response(status_code=204)
