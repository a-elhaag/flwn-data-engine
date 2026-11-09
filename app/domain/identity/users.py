"""Trusted user-account CRUD. Users are global: one person can be a member of many workspaces.

Emails are stored in canonical form only (app/identity/email.py).
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, text

import app.db.models  # noqa: F401  (registers every model on Base.metadata)
from app.db import events
from app.db.base import Base
from app.db.models.identity import Member, TeamMember, User, Workspace
from app.db.session import service_session, session_for
from app.domain.identity.email import canonical_email, canonical_or_none
from app.domain.identity.owners import LastOwner, active_owners, lock_owners

EMAIL_TAKEN = "email_taken"


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


def _has_work(session, member_id) -> bool:
    """True if any row still points at the member, other than their team seats."""
    for table in Base.metadata.sorted_tables:
        if table.name in ("members", "team_members"):
            continue
        for column in table.columns:
            if any(fk.column.table.name == "members" for fk in column.foreign_keys):
                if session.scalar(select(column).where(column == member_id).limit(1)) is not None:
                    return True
    return False


def email_matches(email: str):
    """The users.email filter for a canonical address.

    Stored emails are already canonical (lowercase), so lowercasing both sides changes nothing
    for them. It is kept on purpose: uq_users_email is an index on lower(email), and this exact
    expression is what lets Postgres use it.
    """
    return func.lower(User.email) == func.lower(email)


def _email_available(session, email, *, exclude_id=None):
    query = select(User.id).where(email_matches(email))
    if exclude_id is not None:
        query = query.where(User.id != exclude_id)
    if session.scalar(query) is not None:
        raise UserConflict(EMAIL_TAKEN)


class UserService:
    def list(self, limit=50, offset=0, *, email=None):
        with service_session() as session:
            query = select(User).where(User.deleted_at.is_(None))
            if email is not None:
                canonical = canonical_or_none(email)
                if canonical is None:
                    return []  # no account can have an address that is not canonical
                query = query.where(email_matches(canonical))
            rows = session.scalars(
                query.order_by(User.created_at, User.id).limit(limit).offset(offset)
            )
            return list(rows)

    def get(self, user_id):
        with service_session() as session:
            return _user(session, user_id)

    def create(self, *, email, name, avatar_url=None, settings=None):
        email = canonical_email(email)
        with service_session() as session:
            _email_available(session, email)
            row = User(
                email=email,
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
                fields["email"] = canonical_email(fields["email"])
                _email_available(session, fields["email"], exclude_id=row.id)
            if "name" in fields:
                fields["name"] = fields["name"].strip()
            for key, value in fields.items():
                setattr(row, key, value)
            session.flush()
            return row

    def delete(self, user_id):
        """Soft delete the account and its memberships, in one transaction.

        A membership the person has done work in is suspended, so the work still shows who did it.
        One with no work is removed (its team seats go too). Refused (LastOwner) while they are
        the last active owner of a workspace: make someone else owner first.
        """
        with service_session() as session:
            row = _user(session, user_id)
            owned = session.scalars(
                select(Member.workspace_id).where(
                    Member.user_id == row.id, Member.role == "owner", Member.deleted_at.is_(None)
                )
            ).all()
            before = lock_owners(session, owned)
            for workspace_id, count in before.items():
                if (
                    count
                    and _live(session, workspace_id)
                    and not active_owners(session, workspace_id, excluding_user=row.id)
                ):
                    raise LastOwner()
            now = datetime.now(UTC)
            memberships = session.scalars(
                select(Member).where(Member.user_id == row.id, Member.deleted_at.is_(None))
            ).all()
            for member in memberships:
                if _has_work(session, member.id):
                    member.status = "suspended"
                    action = "suspended"
                else:
                    session.execute(delete(TeamMember).where(TeamMember.member_id == member.id))
                    member.deleted_at = now
                    action = "deleted"
                events.record(session, str(member.workspace_id), None, "member", member.id, action)
            row.deleted_at = now

    def memberships(self, user_id, *, status=None, type=None):
        """The user's memberships in live workspaces, oldest workspace first.

        Runs without service mode: only memberships_for_user crosses workspaces.
        """
        with session_for("") as session:
            _user(session, user_id)
            rows = session.execute(
                text(
                    "select * from memberships_for_user(:user)"
                    " where (cast(:status as text) is null or status = :status)"
                    " and (cast(:type as text) is null or type = :type)"
                ),
                {"user": uuid.UUID(str(user_id)), "status": status, "type": type},
            ).mappings()
            return [dict(row) for row in rows]


def _live(session, workspace_id) -> bool:
    found = session.scalar(
        select(Workspace.id).where(Workspace.id == workspace_id, Workspace.deleted_at.is_(None))
    )
    return found is not None
