"""Trusted CRUD for user accounts (backend only: users belong to no single workspace)."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.exc import IntegrityError

from app.api.auth import service_only
from app.api.errors import api_error, code_of
from app.api.paging import split_page
from app.api.schemas.identity import MembershipList, UserCreate, UserOut, UserPage, UserUpdate
from app.domain.identity.owners import LAST_OWNER, LastOwner
from app.domain.identity.users import EMAIL_TAKEN, UserConflict, UserNotFound, UserService

router = APIRouter(prefix="/users", dependencies=[Depends(service_only)])


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except UserNotFound as exc:
        raise api_error(404, exc.code, str(exc)) from None
    except UserConflict as exc:
        raise api_error(409, exc.code, str(exc)) from None
    except LastOwner as exc:
        raise api_error(409, exc.code, LAST_OWNER) from None
    except IntegrityError as exc:
        if "uq_users_email" in str(exc.orig):  # lost a race with another create
            raise api_error(409, "email_taken", EMAIL_TAKEN) from None
        raise api_error(409, "conflict_user", "user conflicts with an existing record") from None
    except ValueError as exc:
        raise api_error(422, code_of(exc, "invalid_value"), str(exc)) from None


@router.post("", status_code=201, response_model=UserOut)
def create_user(body: UserCreate):
    return _call(UserService().create, **body.model_dump())


@router.get("", response_model=UserPage)
def list_users(
    email: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    items = _call(UserService().list, limit + 1, offset, email=email)
    items, has_more = split_page(items, limit)
    return UserPage(items=items, limit=limit, offset=offset, has_more=has_more)


@router.get("/{user_id}", response_model=UserOut)
def get_user(user_id: UUID):
    return _call(UserService().get, str(user_id))


@router.get(
    "/{user_id}/memberships",
    response_model=MembershipList,
    responses={404: {"description": "User not found"}},
)
def list_user_memberships(
    user_id: UUID,
    status: Literal["active", "suspended"] | None = None,
    type: Literal["HUMAN", "AI"] | None = None,
):
    items = _call(UserService().memberships, str(user_id), status=status, type=type)
    return MembershipList(items=items, total=len(items))


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: UUID, body: UserUpdate):
    return _call(UserService().update, str(user_id), **body.model_dump(exclude_unset=True))


@router.delete(
    "/{user_id}",
    status_code=204,
    responses={409: {"description": "last_owner: the user is a workspace's last active owner"}},
)
def delete_user(user_id: UUID):
    _call(UserService().delete, str(user_id))
    return Response(status_code=204)
