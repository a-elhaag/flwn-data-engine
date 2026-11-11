"""Trusted CRUD for workspace members and teams."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy.exc import IntegrityError

from app.api import tokens
from app.api.auth import WorkspaceId, actor, allow, viewer
from app.api.errors import api_error, code_of
from app.api.paging import split_page
from app.api.schemas.identity import (
    InvitationAccept,
    InvitationCreate,
    InvitationCreated,
    InvitationOut,
    InvitationPage,
    MemberCreate,
    MemberOut,
    MemberPage,
    MemberUpdate,
    OwnershipTransfer,
    OwnershipTransferOut,
    TeamCreate,
    TeamMemberCreate,
    TeamMemberOut,
    TeamMemberPage,
    TeamMembersUpdate,
    TeamMemberUpdate,
    TeamOut,
    TeamPage,
    TeamUpdate,
)
from app.domain.identity.invitations import InvitationService
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
        raise api_error(404, exc.code, str(exc)) from None
    except IdentityConflict as exc:
        raise api_error(409, exc.code, str(exc)) from None
    except IdentityForbidden as exc:
        raise api_error(403, exc.code, str(exc)) from None
    except LastOwner as exc:
        raise api_error(409, exc.code, LAST_OWNER) from None
    except IntegrityError as exc:
        if LAST_OWNER in str(exc.orig):  # the database backstop (db/sql/70_identity.sql)
            raise api_error(409, LAST_OWNER, LAST_OWNER) from None
        if "uq_users_email" in str(exc.orig):  # lost a race: another call created that user first
            raise api_error(409, "email_taken", EMAIL_TAKEN) from None
        raise api_error(
            409, "conflict_record", "resource conflicts with an existing record"
        ) from None
    except ValueError as exc:
        raise api_error(422, code_of(exc, "invalid_value"), str(exc)) from None


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


@router.get("/teams/{team_id}/members", response_model=TeamMemberPage)
def list_team_members(
    request: Request,
    workspace_id: WorkspaceId,
    team_id: UUID,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    items = _call(
        TeamService(str(workspace_id), viewer(request)).members, str(team_id), limit + 1, offset
    )
    items, has_more = split_page(items, limit)
    return TeamMemberPage(items=items, limit=limit, offset=offset, has_more=has_more)


@router.post(
    "/teams/{team_id}/members",
    status_code=201,
    response_model=TeamMemberOut,
    dependencies=[ADMIN],
)
def add_team_member(
    request: Request, workspace_id: WorkspaceId, team_id: UUID, body: TeamMemberCreate
):
    return _call(
        TeamService(str(workspace_id), viewer(request)).add_member,
        str(team_id),
        str(body.member_id),
        body.role,
    )


@router.patch(
    "/teams/{team_id}/members/{member_id}", response_model=TeamMemberOut, dependencies=[ADMIN]
)
def change_team_member_role(
    request: Request,
    workspace_id: WorkspaceId,
    team_id: UUID,
    member_id: UUID,
    body: TeamMemberUpdate,
):
    return _call(
        TeamService(str(workspace_id), viewer(request)).change_member_role,
        str(team_id),
        str(member_id),
        body.role,
    )


@router.delete("/teams/{team_id}/members/{member_id}", status_code=204, dependencies=[ADMIN])
def remove_team_member(request: Request, workspace_id: WorkspaceId, team_id: UUID, member_id: UUID):
    _call(
        TeamService(str(workspace_id), viewer(request)).remove_member,
        str(team_id),
        str(member_id),
    )
    return Response(status_code=204)


@router.get("/members/{member_id}/teams", response_model=TeamPage)
def list_member_teams(
    request: Request,
    workspace_id: WorkspaceId,
    member_id: UUID,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    items = _call(
        TeamService(str(workspace_id), viewer(request)).for_member,
        str(member_id),
        limit + 1,
        offset,
    )
    items, has_more = split_page(items, limit)
    return TeamPage(items=items, limit=limit, offset=offset, has_more=has_more)


@router.post(
    "/invitations",
    status_code=201,
    response_model=InvitationCreated,
    dependencies=[ADMIN],
)
def create_invitation(request: Request, workspace_id: WorkspaceId, body: InvitationCreate):
    invitation, token = _call(
        InvitationService(str(workspace_id), actor(request)).create, **body.model_dump()
    )
    return {**invitation, "token": token}


@router.get("/invitations", response_model=InvitationPage, dependencies=[ADMIN])
def list_invitations(
    workspace_id: WorkspaceId,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    items = _call(InvitationService(str(workspace_id)).list, limit + 1, offset)
    items, has_more = split_page(items, limit)
    return InvitationPage(items=items, limit=limit, offset=offset, has_more=has_more)


@router.post(
    "/invitations/{invitation_id}/revoke", response_model=InvitationOut, dependencies=[ADMIN]
)
def revoke_invitation(request: Request, workspace_id: WorkspaceId, invitation_id: UUID):
    return _call(InvitationService(str(workspace_id), actor(request)).revoke, str(invitation_id))


# Backend only: the backend relays the person's sign-in, so no workspace token can accept.
@router.post("/invitations/accept", response_model=MemberOut, dependencies=[allow(None)])
def accept_invitation(workspace_id: WorkspaceId, body: InvitationAccept):
    return _call(InvitationService(str(workspace_id)).accept, body.token, body.name)
