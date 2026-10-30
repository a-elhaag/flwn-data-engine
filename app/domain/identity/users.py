"""Trusted user-account CRUD. Users are global: one person can be a member of many workspaces."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.db.models.identity import User
from app.db.session import service_session


class UserNotFound(LookupError):
    pass


class UserConflict(ValueError):
    pass


def _user(session, user_id):
    row = session.scalar(
        select(User).where(User.id == uuid.UUID(str(user_id)), User.deleted_at.is_(None))
    )
    if row is None:
        raise UserNotFound("user not found")
    return row


def _email_available(session, email, *, exclude_id=None):
    query = select(User.id).where(func.lower(User.email) == email.lower())
    if exclude_id is not None:
        query = query.where(User.id != exclude_id)
    if session.scalar(query) is not None:
        raise UserConflict("user email already exists")


class UserService:
    def list(self, limit=50, offset=0, *, email=None):
        with service_session() as session:
            query = select(User).where(User.deleted_at.is_(None))
            if email is not None:
                query = query.where(func.lower(User.email) == email.strip().lower())
            rows = session.scalars(
                query.order_by(User.created_at, User.id).limit(limit).offset(offset)
            )
            return list(rows)

    def get(self, user_id):
        with service_session() as session:
            return _user(session, user_id)

    def create(self, *, email, name, avatar_url=None, settings=None):
        with service_session() as session:
            _email_available(session, email)
            row = User(
                email=email.strip(),
                name=name.strip(),
                avatar_url=avatar_url,
                settings=settings or {},
            )
            session.add(row)
            session.flush()
            return row

    def update(self, user_id, **fields):
        with service_session() as session:
            row = _user(session, user_id)
            if "email" in fields:
                fields["email"] = fields["email"].strip()
                _email_available(session, fields["email"], exclude_id=row.id)
            if "name" in fields:
                fields["name"] = fields["name"].strip()
            for key, value in fields.items():
                setattr(row, key, value)
            session.flush()
            return row

    def delete(self, user_id):
        """Soft delete. Their memberships are untouched; remove or suspend those separately."""
        with service_session() as session:
            _user(session, user_id).deleted_at = datetime.now(UTC)
