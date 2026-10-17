from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.models import Member, User
from domain.crud import apply_update, fetch_page, save
from domain.errors import not_found
from schemas.user import UserCreate, UserUpdate


def create_user(session: Session, data: UserCreate) -> User:
    return save(session, User(**data.model_dump()))


def get_user(session: Session, user_id: UUID) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise not_found("USER")
    return user


def find_users_by_email(session: Session, email: str, limit: int, offset: int):
    stmt = (
        select(User)
        .where(func.lower(User.email) == email.strip().lower())
        .order_by(User.created_at, User.id)
    )
    return fetch_page(session, stmt, limit, offset)


def update_user(session: Session, user_id: UUID, data: UserUpdate) -> User:
    return apply_update(session, get_user(session, user_id), data)


def list_memberships(session: Session, user_id: UUID, limit: int, offset: int):
    get_user(session, user_id)
    stmt = (
        select(Member)
        .where(Member.user_id == user_id)
        .order_by(Member.created_at, Member.id)
    )
    return fetch_page(session, stmt, limit, offset)
