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
    OwnershipTransfer,
    OwnershipTransferOut,
    TeamCreate,
    TeamMembersUpdate,
    TeamOut,
    TeamPage,
    TeamUpdate,
)
from app.domain.identity.members_teams import (
    IdentityConflict,
    IdentityForbidden,
    IdentityNotFound,
    MemberService,
    TeamService,
)
from app.domain.identity.owners import LAST_OWNER, LastOwner
from app.domain.identity.users import EMAIL_TAKEN

router = APIRouter(
    prefix="/workspaces/{workspace_id}",
    dependencies=[allow(tokens.SCOPE_IDENTITY_READ, write_scope=tokens.SCOPE_IDENTITY_WRITE)],
)
# Changing who belongs to the workspace or to a team is for administrators.
ADMIN = allow(tokens.SCOPE_ADMIN)
LAST_OWNER_409 = {409: {"description": "last_owner: the workspace would have no active owner"}}


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except IdentityNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    except IdentityConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except IdentityForbidden as exc:
        raise HTTPException(403, str(exc)) from None
    except LastOwner:
        raise HTTPException(409, LAST_OWNER) from None
    except IntegrityError as exc:
        if LAST_OWNER in str(exc.orig):  # the database backstop (db/sql/70_identity.sql)
            raise HTTPException(409, LAST_OWNER) from None
        if "uq_users_email" in str(exc.orig):  # lost a race: another call created that user first
            raise HTTPException(409, EMAIL_TAKEN) from None
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


@router.patch(
    "/members/{member_id}", response_model=MemberOut, dependencies=[ADMIN], responses=LAST_OWNER_409
)
def update_member(request: Request, workspace_id: WorkspaceId, member_id: UUID, body: MemberUpdate):
    return _call(
        MemberService(str(workspace_id), actor(request)).update,
        str(member_id),
        **body.model_dump(exclude_unset=True),
    )


@router.delete(
    "/members/{member_id}", status_code=204, dependencies=[ADMIN], responses=LAST_OWNER_409
)
def delete_member(request: Request, workspace_id: WorkspaceId, member_id: UUID):
    _call(MemberService(str(workspace_id), actor(request)).delete, str(member_id))
    return Response(status_code=204)


@router.post(
    "/ownership/transfer",
    response_model=OwnershipTransferOut,
    dependencies=[ADMIN],
    responses={
        403: {"description": "the acting member (X-Acting-Member-Id) is not a current owner"},
        **LAST_OWNER_409,
        422: {"description": "the target is not an active human member of this workspace"},
    },
)
def transfer_ownership(request: Request, workspace_id: WorkspaceId, body: OwnershipTransfer):
    return _call(
        MemberService(str(workspace_id), actor(request)).transfer_ownership,
        str(body.to_member_id),
        body.demote_self_to,
    )


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
