"""Trusted CRUD operations for workspaces."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.exc import IntegrityError

from app.api import tokens
from app.api.auth import actor, allow, service_only
from app.api.identity_schemas import WorkspaceCreate, WorkspaceOut, WorkspacePage, WorkspaceUpdate
from app.identity.workspaces import WorkspaceConflict, WorkspaceNotFound, WorkspaceService

router = APIRouter(prefix="/workspaces")
# Creating and listing workspaces happens before any workspace token exists: backend only.
BACKEND = Depends(service_only)


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except WorkspaceNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    except WorkspaceConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except IntegrityError:
        raise HTTPException(409, "workspace conflicts with an existing record") from None


@router.post("", status_code=201, response_model=WorkspaceOut, dependencies=[BACKEND])
def create_workspace(body: WorkspaceCreate):
    return _call(WorkspaceService().create, **body.model_dump())


@router.get("", response_model=WorkspacePage, dependencies=[BACKEND])
def list_workspaces(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    items = _call(WorkspaceService().list, limit, offset)
    return WorkspacePage(items=items, limit=limit, offset=offset)


@router.get(
    "/{workspace_id}",
    response_model=WorkspaceOut,
    dependencies=[allow(tokens.SCOPE_IDENTITY_READ, service=False)],
)
def get_workspace(workspace_id: UUID):
    return _call(WorkspaceService().get, str(workspace_id))


@router.patch(
    "/{workspace_id}",
    response_model=WorkspaceOut,
    dependencies=[allow(tokens.SCOPE_ADMIN, service=False)],
)
def update_workspace(request: Request, workspace_id: UUID, body: WorkspaceUpdate):
    return _call(
        WorkspaceService(actor(request)).update,
        str(workspace_id),
        **body.model_dump(exclude_unset=True),
    )


@router.delete(
    "/{workspace_id}", status_code=204, dependencies=[allow(tokens.SCOPE_ADMIN, service=False)]
)
def delete_workspace(request: Request, workspace_id: UUID):
    _call(WorkspaceService(actor(request)).delete, str(workspace_id))
    return Response(status_code=204)
