"""Trusted CRUD for workspace projects, work items, and tasks."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.exc import IntegrityError

from app.api.auth import WorkspaceId, service_only
from app.api.planning_schemas import (
    ProjectCreate,
    ProjectOut,
    ProjectPage,
    ProjectUpdate,
    TaskCreate,
    TaskOut,
    TaskPage,
    TaskUpdate,
    WorkItemCreate,
    WorkItemOut,
    WorkItemPage,
    WorkItemUpdate,
)
from app.planning.service import (
    PlanningConflict,
    PlanningNotFound,
    ProjectService,
    TaskService,
    WorkItemService,
)

router = APIRouter(prefix="/workspaces/{workspace_id}", dependencies=[Depends(service_only)])


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
def create_project(workspace_id: WorkspaceId, body: ProjectCreate):
    return _call(ProjectService(str(workspace_id)).create, **body.model_dump())


@router.get("/projects", response_model=ProjectPage)
def list_projects(
    workspace_id: WorkspaceId,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    items = _call(ProjectService(str(workspace_id)).list, limit, offset)
    return ProjectPage(items=items, limit=limit, offset=offset)


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(workspace_id: WorkspaceId, project_id: UUID):
    return _call(ProjectService(str(workspace_id)).get, str(project_id))


@router.patch("/projects/{project_id}", response_model=ProjectOut)
def update_project(workspace_id: WorkspaceId, project_id: UUID, body: ProjectUpdate):
    return _call(
        ProjectService(str(workspace_id)).update,
        str(project_id),
        **body.model_dump(exclude_unset=True),
    )


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(workspace_id: WorkspaceId, project_id: UUID):
    _call(ProjectService(str(workspace_id)).delete, str(project_id))
    return Response(status_code=204)


@router.post("/projects/{project_id}/work-items", status_code=201, response_model=WorkItemOut)
def create_work_item(workspace_id: WorkspaceId, project_id: UUID, body: WorkItemCreate):
    return _call(WorkItemService(str(workspace_id)).create, str(project_id), **body.model_dump())


@router.get("/projects/{project_id}/work-items", response_model=WorkItemPage)
def list_work_items(
    workspace_id: WorkspaceId,
    project_id: UUID,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    items = _call(WorkItemService(str(workspace_id)).list, str(project_id), limit, offset)
    return WorkItemPage(items=items, limit=limit, offset=offset)


@router.get("/projects/{project_id}/work-items/{work_item_id}", response_model=WorkItemOut)
def get_work_item(workspace_id: WorkspaceId, project_id: UUID, work_item_id: UUID):
    return _call(WorkItemService(str(workspace_id)).get, str(project_id), str(work_item_id))


@router.patch("/projects/{project_id}/work-items/{work_item_id}", response_model=WorkItemOut)
def update_work_item(
    workspace_id: WorkspaceId, project_id: UUID, work_item_id: UUID, body: WorkItemUpdate
):
    return _call(
        WorkItemService(str(workspace_id)).update,
        str(project_id),
        str(work_item_id),
        **body.model_dump(exclude_unset=True),
    )


@router.delete("/projects/{project_id}/work-items/{work_item_id}", status_code=204)
def delete_work_item(workspace_id: WorkspaceId, project_id: UUID, work_item_id: UUID):
    _call(WorkItemService(str(workspace_id)).delete, str(project_id), str(work_item_id))
    return Response(status_code=204)


@router.post("/projects/{project_id}/tasks", status_code=201, response_model=TaskOut)
def create_task(workspace_id: WorkspaceId, project_id: UUID, body: TaskCreate):
    return _call(TaskService(str(workspace_id)).create, str(project_id), **body.model_dump())


@router.get("/projects/{project_id}/tasks", response_model=TaskPage)
def list_tasks(
    workspace_id: WorkspaceId,
    project_id: UUID,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    items = _call(TaskService(str(workspace_id)).list, str(project_id), limit, offset)
    return TaskPage(items=items, limit=limit, offset=offset)


@router.get("/projects/{project_id}/tasks/{task_id}", response_model=TaskOut)
def get_task(workspace_id: WorkspaceId, project_id: UUID, task_id: UUID):
    return _call(TaskService(str(workspace_id)).get, str(project_id), str(task_id))


@router.patch("/projects/{project_id}/tasks/{task_id}", response_model=TaskOut)
def update_task(workspace_id: WorkspaceId, project_id: UUID, task_id: UUID, body: TaskUpdate):
    return _call(
        TaskService(str(workspace_id)).update,
        str(project_id),
        str(task_id),
        **body.model_dump(exclude_unset=True),
    )


@router.delete("/projects/{project_id}/tasks/{task_id}", status_code=204)
def delete_task(workspace_id: WorkspaceId, project_id: UUID, task_id: UUID):
    _call(TaskService(str(workspace_id)).delete, str(project_id), str(task_id))
    return Response(status_code=204)
