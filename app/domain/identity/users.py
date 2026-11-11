"""Trusted user-account CRUD. Users are global: one person can be a member of many workspaces.

Emails are stored in canonical form only (app/identity/email.py).
"""

import uuid

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

import app.db.models  # noqa: F401  (registers every model on Base.metadata)
from app.db import events
from app.db.models.identity import Member, User, Workspace
from app.db.session import service_session, session_for
from app.domain.error_codes import DomainError
from app.domain.identity.email import canonical_email, canonical_or_none
from app.domain.identity.owners import LastOwner, active_owners, lock_owners

EMAIL_TAKEN = "email_taken"


class UserNotFound(DomainError, LookupError):
    code = "user_not_found"


class UserConflict(DomainError, ValueError):
    code = "user_conflict"


class UserInUse(UserConflict):
    code = "user_in_use"


def _user(session, user_id):
    row = session.scalar(select(User).where(User.id == uuid.UUID(str(user_id))))
    if row is None:
        raise UserNotFound("user not found", code="user_not_found")
    return row


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
        raise UserConflict(EMAIL_TAKEN, code="email_taken")


class UserService:
    def list(self, limit=50, offset=0, *, email=None):
        with service_session() as session:
            query = select(User)
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
        """Delete the account and every membership, in one transaction.

        Each membership goes the way a member delete does: its team seats go, and references to
        it (assignees, authors) become null, while the audit log keeps a snapshot of the name.
        Refused (LastOwner) while they are the last active owner of a workspace: make someone
        else owner first. Refused (UserInUse) while they still own atoms, skills or versions.
        """
        with service_session() as session:
            row = _user(session, user_id)
            owned = session.scalars(
                select(Member.workspace_id).where(Member.user_id == row.id, Member.role == "owner")
            ).all()
            before = lock_owners(session, owned)
            for workspace_id, count in before.items():
                if (
                    count
                    and _live(session, workspace_id)
                    and not active_owners(session, workspace_id, excluding_user=row.id)
                ):
                    raise LastOwner()
            memberships = session.scalars(select(Member).where(Member.user_id == row.id)).all()
            snapshots = [(str(m.workspace_id), m.id, m.type) for m in memberships]
            name = row.name
            try:
                for member in memberships:
                    session.delete(member)
                session.delete(row)
                session.flush()
            except IntegrityError as exc:
                raise UserInUse(
                    "user still owns atoms, skills or versions; transfer or delete them first"
                ) from exc
            for workspace_id, member_id, member_type in snapshots:
                events.record(
                    session,
                    workspace_id,
                    None,
                    "member",
                    member_id,
                    "deleted",
                    {"type": member_type, "user": name},
                )

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
    found = session.scalar(select(Workspace.id).where(Workspace.id == workspace_id))
    return found is not None
