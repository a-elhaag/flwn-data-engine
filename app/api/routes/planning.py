"""Trusted CRUD for workspace projects, work items, sprints, and tasks."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy.exc import IntegrityError

from app.api import tokens
from app.api.auth import WorkspaceId, actor, allow
from app.api.paging import split_page
from app.api.schemas.planning import (
    ProjectCreate,
    ProjectOut,
    ProjectPage,
    ProjectUpdate,
    SprintCreate,
    SprintOut,
    SprintPage,
    SprintUpdate,
    TaskCreate,
    TaskOut,
    TaskPage,
    TaskUpdate,
    WorkItemCreate,
    WorkItemOut,
    WorkItemPage,
    WorkItemUpdate,
)
from app.db.base import WORK_STATUSES
from app.domain.planning.service import (
    PlanningConflict,
    PlanningNotFound,
    ProjectService,
    SprintService,
    TaskService,
    WorkItemService,
)

router = APIRouter(
    prefix="/workspaces/{workspace_id}",
    dependencies=[allow(tokens.SCOPE_PLANNING_READ, write_scope=tokens.SCOPE_PLANNING_WRITE)],
)


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except PlanningNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    except PlanningConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except IntegrityError:
        raise HTTPException(409, "resource conflicts with an existing record") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/projects", status_code=201, response_model=ProjectOut)
def create_project(request: Request, workspace_id: WorkspaceId, body: ProjectCreate):
    return _call(ProjectService(str(workspace_id), actor(request)).create, **body.model_dump())


@router.get("/projects", response_model=ProjectPage)
def list_projects(
    request: Request,
    workspace_id: WorkspaceId,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    items = _call(ProjectService(str(workspace_id), actor(request)).list, limit + 1, offset)
    items, has_more = split_page(items, limit)
    return ProjectPage(items=items, limit=limit, offset=offset, has_more=has_more)


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(request: Request, workspace_id: WorkspaceId, project_id: UUID):
    return _call(ProjectService(str(workspace_id), actor(request)).get, str(project_id))


@router.patch("/projects/{project_id}", response_model=ProjectOut)
def update_project(
    request: Request, workspace_id: WorkspaceId, project_id: UUID, body: ProjectUpdate
):
    return _call(
        ProjectService(str(workspace_id), actor(request)).update,
        str(project_id),
        **body.model_dump(exclude_unset=True),
    )


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(request: Request, workspace_id: WorkspaceId, project_id: UUID):
    _call(ProjectService(str(workspace_id), actor(request)).delete, str(project_id))
    return Response(status_code=204)


@router.post("/projects/{project_id}/work-items", status_code=201, response_model=WorkItemOut)
def create_work_item(
    request: Request, workspace_id: WorkspaceId, project_id: UUID, body: WorkItemCreate
):
    return _call(
        WorkItemService(str(workspace_id), actor(request)).create,
        str(project_id),
        **body.model_dump(),
    )


@router.get("/projects/{project_id}/work-items", response_model=WorkItemPage)
def list_work_items(
    request: Request,
    workspace_id: WorkspaceId,
    project_id: UUID,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    items = _call(
        WorkItemService(str(workspace_id), actor(request)).list, str(project_id), limit + 1, offset
    )
    items, has_more = split_page(items, limit)
    return WorkItemPage(items=items, limit=limit, offset=offset, has_more=has_more)


@router.get("/projects/{project_id}/work-items/{work_item_id}", response_model=WorkItemOut)
def get_work_item(
    request: Request, workspace_id: WorkspaceId, project_id: UUID, work_item_id: UUID
):
    return _call(
        WorkItemService(str(workspace_id), actor(request)).get, str(project_id), str(work_item_id)
    )


@router.patch("/projects/{project_id}/work-items/{work_item_id}", response_model=WorkItemOut)
def update_work_item(
    request: Request,
    workspace_id: WorkspaceId,
    project_id: UUID,
    work_item_id: UUID,
    body: WorkItemUpdate,
):
    return _call(
        WorkItemService(str(workspace_id), actor(request)).update,
        str(project_id),
        str(work_item_id),
        **body.model_dump(exclude_unset=True),
    )


@router.delete("/projects/{project_id}/work-items/{work_item_id}", status_code=204)
def delete_work_item(
    request: Request, workspace_id: WorkspaceId, project_id: UUID, work_item_id: UUID
):
    _call(
        WorkItemService(str(workspace_id), actor(request)).delete,
        str(project_id),
        str(work_item_id),
    )
    return Response(status_code=204)


@router.post("/projects/{project_id}/tasks", status_code=201, response_model=TaskOut)
def create_task(request: Request, workspace_id: WorkspaceId, project_id: UUID, body: TaskCreate):
    return _call(
        TaskService(str(workspace_id), actor(request)).create, str(project_id), **body.model_dump()
    )


@router.get("/projects/{project_id}/tasks", response_model=TaskPage)
def list_tasks(
    request: Request,
    workspace_id: WorkspaceId,
    project_id: UUID,
    assignee_id: UUID | None = None,
    status: str | None = None,
    sprint_id: UUID | None = None,
    backlog: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    if status is not None and status not in WORK_STATUSES:
        raise HTTPException(422, "invalid status")
    items = _call(
        TaskService(str(workspace_id), actor(request)).list,
        str(project_id),
        limit + 1,
        offset,
        assignee_id=assignee_id,
        status=status,
        sprint_id=sprint_id,
        backlog=backlog,
    )
    items, has_more = split_page(items, limit)
    return TaskPage(items=items, limit=limit, offset=offset, has_more=has_more)


@router.get("/projects/{project_id}/tasks/{task_id}", response_model=TaskOut)
def get_task(request: Request, workspace_id: WorkspaceId, project_id: UUID, task_id: UUID):
    return _call(TaskService(str(workspace_id), actor(request)).get, str(project_id), str(task_id))


@router.patch("/projects/{project_id}/tasks/{task_id}", response_model=TaskOut)
def update_task(
    request: Request, workspace_id: WorkspaceId, project_id: UUID, task_id: UUID, body: TaskUpdate
):
    return _call(
        TaskService(str(workspace_id), actor(request)).update,
        str(project_id),
        str(task_id),
        **body.model_dump(exclude_unset=True),
    )


@router.delete("/projects/{project_id}/tasks/{task_id}", status_code=204)
def delete_task(request: Request, workspace_id: WorkspaceId, project_id: UUID, task_id: UUID):
    _call(TaskService(str(workspace_id), actor(request)).delete, str(project_id), str(task_id))
    return Response(status_code=204)


@router.post("/projects/{project_id}/sprints", status_code=201, response_model=SprintOut)
def create_sprint(
    request: Request, workspace_id: WorkspaceId, project_id: UUID, body: SprintCreate
):
    return _call(
        SprintService(str(workspace_id), actor(request)).create,
        str(project_id),
        **body.model_dump(),
    )


@router.get("/projects/{project_id}/sprints", response_model=SprintPage)
def list_sprints(
    request: Request,
    workspace_id: WorkspaceId,
    project_id: UUID,
    status: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    items = _call(
        SprintService(str(workspace_id), actor(request)).list,
        str(project_id),
        status,
        limit + 1,
        offset,
    )
    items, has_more = split_page(items, limit)
    return SprintPage(items=items, limit=limit, offset=offset, has_more=has_more)


@router.get("/projects/{project_id}/sprints/{sprint_id}", response_model=SprintOut)
def get_sprint(request: Request, workspace_id: WorkspaceId, project_id: UUID, sprint_id: UUID):
    return _call(
        SprintService(str(workspace_id), actor(request)).get, str(project_id), str(sprint_id)
    )


@router.patch("/projects/{project_id}/sprints/{sprint_id}", response_model=SprintOut)
def update_sprint(
    request: Request,
    workspace_id: WorkspaceId,
    project_id: UUID,
    sprint_id: UUID,
    body: SprintUpdate,
):
    return _call(
        SprintService(str(workspace_id), actor(request)).update,
        str(project_id),
        str(sprint_id),
        **body.model_dump(exclude_unset=True),
    )


@router.delete("/projects/{project_id}/sprints/{sprint_id}", status_code=204)
def delete_sprint(request: Request, workspace_id: WorkspaceId, project_id: UUID, sprint_id: UUID):
    _call(SprintService(str(workspace_id), actor(request)).delete, str(project_id), str(sprint_id))
    return Response(status_code=204)


@router.get("/tasks/by-key/{key}", response_model=TaskOut)
def get_task_by_key(request: Request, workspace_id: WorkspaceId, key: str):
    return _call(TaskService(str(workspace_id), actor(request)).by_key, key)


@router.get("/members/{member_id}/tasks", response_model=TaskPage)
def list_member_tasks(
    request: Request,
    workspace_id: WorkspaceId,
    member_id: UUID,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    items = _call(
        TaskService(str(workspace_id), actor(request)).assigned_to,
        str(member_id),
        limit + 1,
        offset,
    )
    items, has_more = split_page(items, limit)
    return TaskPage(items=items, limit=limit, offset=offset, has_more=has_more)


@router.get("/members/{member_id}/projects", response_model=ProjectPage)
def list_member_projects(
    request: Request,
    workspace_id: WorkspaceId,
    member_id: UUID,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    items = _call(
        ProjectService(str(workspace_id), actor(request)).for_member,
        str(member_id),
        limit + 1,
        offset,
    )
    items, has_more = split_page(items, limit)
    return ProjectPage(items=items, limit=limit, offset=offset, has_more=has_more)
