"""Trusted CRUD for user accounts (backend only: users belong to no single workspace)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.exc import IntegrityError

from app.api.auth import service_only
from app.api.identity_schemas import UserCreate, UserOut, UserPage, UserUpdate
from app.identity.users import UserConflict, UserNotFound, UserService

router = APIRouter(prefix="/users", dependencies=[Depends(service_only)])


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except UserNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    except UserConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except IntegrityError:
        raise HTTPException(409, "user conflicts with an existing record") from None


@router.post("", status_code=201, response_model=UserOut)
def create_user(body: UserCreate):
    return _call(UserService().create, **body.model_dump())


@router.get("", response_model=UserPage)
def list_users(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    items = _call(UserService().list, limit, offset)
    return UserPage(items=items, limit=limit, offset=offset)


@router.get("/{user_id}", response_model=UserOut)
def get_user(user_id: UUID):
    return _call(UserService().get, str(user_id))


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: UUID, body: UserUpdate):
    return _call(UserService().update, str(user_id), **body.model_dump(exclude_unset=True))


@router.delete("/{user_id}", status_code=204)
def delete_user(user_id: UUID):
    _call(UserService().delete, str(user_id))
    return Response(status_code=204)
