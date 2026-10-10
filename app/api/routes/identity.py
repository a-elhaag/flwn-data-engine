"""Trusted CRUD for workspace members and teams."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.exc import IntegrityError

from app.api.auth import WorkspaceId, service_only
from app.api.identity_schemas import (
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
from app.identity.members_teams import (
    IdentityConflict,
    IdentityNotFound,
    MemberService,
    TeamService,
)

router = APIRouter(prefix="/workspaces/{workspace_id}", dependencies=[Depends(service_only)])


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


@router.post("/members", status_code=201, response_model=MemberOut)
def create_member(workspace_id: WorkspaceId, body: MemberCreate):
    return _call(MemberService(str(workspace_id)).create, **body.model_dump())


@router.get("/members", response_model=MemberPage)
def list_members(
    workspace_id: WorkspaceId,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    items = _call(MemberService(str(workspace_id)).list, limit, offset)
    return MemberPage(items=items, limit=limit, offset=offset)


@router.get("/members/{member_id}", response_model=MemberOut)
def get_member(workspace_id: WorkspaceId, member_id: UUID):
    return _call(MemberService(str(workspace_id)).get, str(member_id))


@router.patch("/members/{member_id}", response_model=MemberOut)
def update_member(workspace_id: WorkspaceId, member_id: UUID, body: MemberUpdate):
    return _call(
        MemberService(str(workspace_id)).update,
        str(member_id),
        **body.model_dump(exclude_unset=True),
    )


@router.delete("/members/{member_id}", status_code=204)
def delete_member(workspace_id: WorkspaceId, member_id: UUID):
    _call(MemberService(str(workspace_id)).delete, str(member_id))
    return Response(status_code=204)


@router.post("/teams", status_code=201, response_model=TeamOut)
def create_team(workspace_id: WorkspaceId, body: TeamCreate):
    return _call(TeamService(str(workspace_id)).create, **body.model_dump())


@router.get("/teams", response_model=TeamPage)
def list_teams(
    workspace_id: WorkspaceId,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    items = _call(TeamService(str(workspace_id)).list, limit, offset)
    return TeamPage(items=items, limit=limit, offset=offset)


@router.get("/teams/{team_id}", response_model=TeamOut)
def get_team(workspace_id: WorkspaceId, team_id: UUID):
    return _call(TeamService(str(workspace_id)).get, str(team_id))


@router.patch("/teams/{team_id}", response_model=TeamOut)
def update_team(workspace_id: WorkspaceId, team_id: UUID, body: TeamUpdate):
    return _call(
        TeamService(str(workspace_id)).update,
        str(team_id),
        **body.model_dump(exclude_unset=True),
    )


@router.put("/teams/{team_id}/members", response_model=TeamOut)
def replace_team_members(workspace_id: WorkspaceId, team_id: UUID, body: TeamMembersUpdate):
    return _call(TeamService(str(workspace_id)).set_members, str(team_id), body.member_ids)


@router.delete("/teams/{team_id}", status_code=204)
def delete_team(workspace_id: WorkspaceId, team_id: UUID):
    _call(TeamService(str(workspace_id)).delete, str(team_id))
    return Response(status_code=204)
