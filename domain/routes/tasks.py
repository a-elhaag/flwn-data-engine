import re
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from db.enums import Priority, TaskStatus
from domain.crud import tasks as tasks_crud
from domain.deps import OrgId, PageParams, ServiceKey, SessionDep
from domain.errors import DomainError, DomainRoute, error_responses
from domain.pagination import Page, make_page
from schemas.task import TaskCreate, TaskRead, TaskUpdate

router = APIRouter(
    tags=["tasks"], route_class=DomainRoute, dependencies=[ServiceKey]
)

_KEY = re.compile(r"^([A-Z]{2,10})-([1-9][0-9]*)$")


@router.post(
    "/projects/{projectId}/tasks",
    status_code=201,
    response_model=TaskRead,
    summary="Create a task",
    description="The task number is assigned from the project's counter in the same "
    "transaction (1, 2, 3...; never reused). The response includes key, e.g. FLWN-12.",
    responses=error_responses(400, 404, 422),
)
def create_task(
    projectId: UUID, body: TaskCreate, org_id: OrgId, session: SessionDep  # noqa: N803
):
    return tasks_crud.create_task(session, org_id, projectId, body)


@router.get(
    "/projects/{projectId}/tasks",
    response_model=Page[TaskRead],
    summary="List tasks of a project",
    description="Ordered by number. `backlog=true` returns tasks without a sprint.",
    responses=error_responses(400, 404, 422),
)
def list_tasks(
    projectId: UUID,  # noqa: N803
    org_id: OrgId,
    session: SessionDep,
    pg: PageParams,
    status: TaskStatus | None = None,
    priority: Priority | None = None,
    assignee_id: Annotated[UUID | None, Query(alias="assigneeId")] = None,
    sprint_id: Annotated[UUID | None, Query(alias="sprintId")] = None,
    work_item_id: Annotated[UUID | None, Query(alias="workItemId")] = None,
    backlog: bool | None = None,
):
    rows, more = tasks_crud.list_tasks(
        session,
        org_id,
        projectId,
        status,
        priority,
        assignee_id,
        sprint_id,
        work_item_id,
        backlog,
        pg.limit,
        pg.offset,
    )
    return make_page(rows, more, pg.limit, pg.offset)


@router.get(
    "/tasks/by-key/{key}",
    response_model=TaskRead,
    summary="Get a task by its key",
    description="Key format PROJECTKEY-NUMBER, e.g. FLWN-12.",
    responses=error_responses(400, 404, 422),
)
def get_task_by_key(key: str, org_id: OrgId, session: SessionDep):
    match = _KEY.match(key)
    if match is None:
        raise DomainError(422, "VALIDATION_ERROR", "key must look like FLWN-12", "key")
    return tasks_crud.get_task_by_key(session, org_id, match.group(1), int(match.group(2)))


@router.get(
    "/tasks/{taskId}",
    response_model=TaskRead,
    summary="Get a task",
    responses=error_responses(400, 404, 422),
)
def get_task(taskId: UUID, org_id: OrgId, session: SessionDep):  # noqa: N803
    return tasks_crud.get_task(session, org_id, taskId)


@router.patch(
    "/tasks/{taskId}",
    response_model=TaskRead,
    summary="Update a task",
    description="Only sent fields change. status DONE requires completedAt (and the "
    "reverse); the database enforces it (422 on completedAt).",
    responses=error_responses(400, 404, 422),
)
def update_task(
    taskId: UUID, body: TaskUpdate, org_id: OrgId, session: SessionDep  # noqa: N803
):
    return tasks_crud.update_task(session, org_id, taskId, body)


@router.delete(
    "/tasks/{taskId}",
    status_code=204,
    summary="Delete a task",
    description="Hard delete. The task number is not reused.",
    responses=error_responses(400, 404, 422),
)
def delete_task(taskId: UUID, org_id: OrgId, session: SessionDep):  # noqa: N803
    tasks_crud.delete_task(session, org_id, taskId)
    return Response(status_code=204)
