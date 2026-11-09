"""Trusted invitation flow: a person joins a workspace only by accepting a valid invitation.

The token is returned once, when the invitation is created. Only its SHA-256 hash is stored.
"""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db import events
from app.db.models.identity import Invitation, Member, User
from app.db.session import session_for
from app.domain.error_codes import Invalid
from app.domain.identity.email import canonical_email
from app.domain.identity.members_teams import (
    IdentityConflict,
    IdentityNotFound,
    _member_view,
    _workspace,
)
from app.domain.identity.users import email_matches

TTL = timedelta(days=7)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _view(row: Invitation) -> dict:
    """The stored status, except a pending invitation past its expiry shows as expired."""
    status = row.status
    if status == "pending" and row.expires_at <= datetime.now(UTC):
        status = "expired"
    return {
        "id": row.id,
        "workspace_id": row.workspace_id,
        "email": row.email,
        "role": row.role,
        "status": status,
        "invited_by": row.invited_by,
        "expires_at": row.expires_at,
        "accepted_at": row.accepted_at,
        "created_at": row.created_at,
    }


class InvitationService:
    def __init__(self, workspace_id, actor=None):
        self.workspace_id = uuid.UUID(str(workspace_id))
        self.actor = str(actor) if actor else None  # who is acting, for the audit log

    def list(self, limit=50, offset=0):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            rows = session.scalars(
                select(Invitation)
                .where(Invitation.workspace_id == self.workspace_id)
                .order_by(Invitation.created_at, Invitation.id)
                .limit(limit)
                .offset(offset)
            )
            return [_view(row) for row in rows]

    def create(self, *, email, role):
        """Returns (invitation, token). The token is not stored, so it cannot be shown again."""
        email = canonical_email(email)
        token = secrets.token_urlsafe(32)
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            active = session.scalar(
                select(Member.id)
                .join(User, User.id == Member.user_id)
                .where(
                    Member.workspace_id == self.workspace_id,
                    email_matches(email),
                    Member.status == "active",
                )
            )
            if active is not None:
                raise IdentityConflict(
                    "user is already an active member of this workspace",
                    code="already_active_member",
                )
            row = session.scalar(
                select(Invitation).where(
                    Invitation.workspace_id == self.workspace_id,
                    Invitation.email == email,
                    Invitation.status == "pending",
                )
            )
            action = "resent" if row else "created"
            if row is None:
                row = Invitation(workspace_id=self.workspace_id, email=email)
                session.add(row)
            # A re-invite replaces the pending one, so the old token stops working at once.
            row.role = role
            row.token_hash = _hash(token)
            row.invited_by = uuid.UUID(self.actor) if self.actor else None
            row.expires_at = datetime.now(UTC) + TTL
            session.flush()
            events.record(
                session,
                str(self.workspace_id),
                self.actor,
                "invitation",
                row.id,
                action,
                {"role": role},
            )
            return _view(row), token

    def revoke(self, invitation_id):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            row = session.scalar(
                select(Invitation)
                .where(
                    Invitation.workspace_id == self.workspace_id,
                    Invitation.id == uuid.UUID(str(invitation_id)),
                )
                .with_for_update()  # one revoke or accept at a time per invitation
            )
            if row is None:
                raise IdentityNotFound("invitation not found", code="invitation_not_found")
            if row.status != "pending":
                raise IdentityConflict(f"invitation is {row.status}", code="invitation_not_pending")
            row.status = "revoked"
            session.flush()
            events.record(
                session, str(self.workspace_id), self.actor, "invitation", row.id, "revoked"
            )
            return _view(row)

    def accept(self, token, name=None):
        """Turns a valid invitation into an active member, in one transaction. Returns the member."""
        now = datetime.now(UTC)
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            row = session.scalar(
                select(Invitation)
                .where(
                    Invitation.workspace_id == self.workspace_id,
                    Invitation.token_hash == _hash(token),
                )
                .with_for_update()  # a token is used once, even under concurrent accepts
            )
            if row is None:
                raise IdentityNotFound("invitation not found", code="invitation_not_found")
            if row.status != "pending":
                raise IdentityConflict(f"invitation is {row.status}", code="invitation_not_pending")
            if row.expires_at <= now:
                raise IdentityConflict("invitation has expired", code="invitation_expired")
            user = session.scalar(select(User).where(email_matches(row.email)))
            if user is None:
                if not name or not name.strip():
                    raise Invalid(
                        "name is required to create the account", code="account_name_required"
                    )
                user = User(email=row.email, name=name.strip())
                session.add(user)
                session.flush()
            # A suspended person gets their old membership row back. A removed one has none.
            member = session.scalar(
                select(Member).where(
                    Member.workspace_id == self.workspace_id, Member.user_id == user.id
                )
            )
            if member is None:
                member = Member(
                    workspace_id=self.workspace_id,
                    user_id=user.id,
                    type="HUMAN",
                    role=row.role,
                    status="active",
                )
                session.add(member)
            elif member.status == "active":
                raise IdentityConflict(
                    "user is already an active member of this workspace",
                    code="already_active_member",
                )
            else:
                member.status = "active"
                member.role = row.role
            row.status = "accepted"
            row.accepted_at = now
            session.flush()
            events.record(
                session,
                str(self.workspace_id),
                None,
                "member",
                member.id,
                "joined",
                {"role": row.role, "invitation_id": str(row.id)},
            )
            return _member_view(session, member)
