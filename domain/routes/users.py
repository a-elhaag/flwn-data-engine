from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from domain.crud import users as users_crud
from domain.deps import PageParams, ServiceKey, SessionDep
from domain.errors import DomainRoute, error_responses
from domain.pagination import Page, make_page
from schemas.member import MemberRead
from schemas.user import UserCreate, UserRead, UserUpdate

router = APIRouter(
    tags=["users"], route_class=DomainRoute, dependencies=[ServiceKey]
)


@router.post(
    "/users",
    status_code=201,
    response_model=UserRead,
    summary="Create a user",
    description="Global login account; exists without any organization. The email is stored lowercased.",
    responses=error_responses(409, 422),
)
def create_user(body: UserCreate, session: SessionDep):
    return users_crud.create_user(session, body)


@router.get(
    "/users",
    response_model=Page[UserRead],
    summary="Find a user by email",
    description="Exact match on the lowercased email. Returns a page with 0 or 1 items.",
    responses=error_responses(422),
)
def find_users(
    email: Annotated[str, Query(min_length=1)], session: SessionDep, pg: PageParams
):
    rows, more = users_crud.find_users_by_email(session, email, pg.limit, pg.offset)
    return make_page(rows, more, pg.limit, pg.offset)


@router.get(
    "/users/{userId}",
    response_model=UserRead,
    summary="Get a user",
    responses=error_responses(404, 422),
)
def get_user(userId: UUID, session: SessionDep):  # noqa: N803
    return users_crud.get_user(session, userId)


@router.patch(
    "/users/{userId}",
    response_model=UserRead,
    summary="Update a user",
    responses=error_responses(404, 409, 422),
)
def update_user(userId: UUID, body: UserUpdate, session: SessionDep):  # noqa: N803
    return users_crud.update_user(session, userId, body)


@router.get(
    "/users/{userId}/memberships",
    response_model=Page[MemberRead],
    summary="List a user's memberships",
    description="The user's Member rows across all organizations.",
    responses=error_responses(404, 422),
)
def list_memberships(userId: UUID, session: SessionDep, pg: PageParams):  # noqa: N803
    rows, more = users_crud.list_memberships(session, userId, pg.limit, pg.offset)
    return make_page(rows, more, pg.limit, pg.offset)
