"""Trusted CRUD for workspace members and teams."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy.exc import IntegrityError

from app.api import tokens
from app.api.auth import WorkspaceId, actor, allow, viewer
from app.api.paging import split_page
from app.api.schemas.identity import (
    MemberCreate,
    MemberOut,
    MemberPage,
    MemberUpdate,
    TeamCreate,
    TeamMembersUpdate,
    TeamOut,
    TeamPage,
    TeamUpdate,
)
from app.domain.identity.members_teams import (
    IdentityConflict,
    IdentityNotFound,
    MemberService,
    TeamService,
)

router = APIRouter(
    prefix="/workspaces/{workspace_id}",
    dependencies=[allow(tokens.SCOPE_IDENTITY_READ, write_scope=tokens.SCOPE_IDENTITY_WRITE)],
)
# Changing who belongs to the workspace or to a team is for administrators.
ADMIN = allow(tokens.SCOPE_ADMIN)


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except IdentityNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    except IdentityConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except IntegrityError:
        raise HTTPException(409, "resource conflicts with an existing record") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/members", status_code=201, response_model=MemberOut, dependencies=[ADMIN])
def create_member(request: Request, workspace_id: WorkspaceId, body: MemberCreate):
    return _call(MemberService(str(workspace_id), actor(request)).create, **body.model_dump())


@router.get("/members", response_model=MemberPage)
def list_members(
    workspace_id: WorkspaceId,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    items = _call(MemberService(str(workspace_id)).list, limit + 1, offset)
    items, has_more = split_page(items, limit)
    return MemberPage(items=items, limit=limit, offset=offset, has_more=has_more)


@router.get("/members/{member_id}", response_model=MemberOut)
def get_member(workspace_id: WorkspaceId, member_id: UUID):
    return _call(MemberService(str(workspace_id)).get, str(member_id))


@router.patch("/members/{member_id}", response_model=MemberOut, dependencies=[ADMIN])
def update_member(request: Request, workspace_id: WorkspaceId, member_id: UUID, body: MemberUpdate):
    return _call(
        MemberService(str(workspace_id), actor(request)).update,
        str(member_id),
        **body.model_dump(exclude_unset=True),
    )


@router.delete("/members/{member_id}", status_code=204, dependencies=[ADMIN])
def delete_member(request: Request, workspace_id: WorkspaceId, member_id: UUID):
    _call(MemberService(str(workspace_id), actor(request)).delete, str(member_id))
    return Response(status_code=204)


@router.post("/teams", status_code=201, response_model=TeamOut)
def create_team(request: Request, workspace_id: WorkspaceId, body: TeamCreate):
    return _call(TeamService(str(workspace_id), viewer(request)).create, **body.model_dump())


@router.get("/teams", response_model=TeamPage)
def list_teams(
    request: Request,
    workspace_id: WorkspaceId,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    items = _call(TeamService(str(workspace_id), viewer(request)).list, limit + 1, offset)
    items, has_more = split_page(items, limit)
    return TeamPage(items=items, limit=limit, offset=offset, has_more=has_more)


@router.get("/teams/{team_id}", response_model=TeamOut)
def get_team(request: Request, workspace_id: WorkspaceId, team_id: UUID):
    return _call(TeamService(str(workspace_id), viewer(request)).get, str(team_id))


@router.patch("/teams/{team_id}", response_model=TeamOut)
def update_team(request: Request, workspace_id: WorkspaceId, team_id: UUID, body: TeamUpdate):
    if "visibility" in body.model_fields_set and not viewer(request).admin:
        raise HTTPException(403, "only an administrator can change a team's visibility")
    return _call(
        TeamService(str(workspace_id), viewer(request)).update,
        str(team_id),
        **body.model_dump(exclude_unset=True),
    )


@router.put("/teams/{team_id}/members", response_model=TeamOut, dependencies=[ADMIN])
def replace_team_members(
    request: Request, workspace_id: WorkspaceId, team_id: UUID, body: TeamMembersUpdate
):
    return _call(
        TeamService(str(workspace_id), viewer(request)).set_members, str(team_id), body.member_ids
    )


@router.delete("/teams/{team_id}", status_code=204, dependencies=[ADMIN])
def delete_team(request: Request, workspace_id: WorkspaceId, team_id: UUID):
    _call(TeamService(str(workspace_id), viewer(request)).delete, str(team_id))
    return Response(status_code=204)
