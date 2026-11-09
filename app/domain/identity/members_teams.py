"""Trusted workspace member and team management."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, exists, false, or_, select, true

from app.db import events
from app.db.models.identity import Member, Team, TeamMember, User, Workspace
from app.db.session import session_for
from app.domain.identity.email import canonical_email
from app.domain.identity.owners import ensure_an_owner, lock_owners
from app.domain.identity.users import email_matches
from app.domain.identity.viewer import Viewer


class IdentityNotFound(LookupError):
    pass


class IdentityConflict(ValueError):
    pass


class IdentityForbidden(PermissionError):
    pass


class InvalidTarget(ValueError):
    pass


def _workspace(session, workspace_id):
    row = session.scalar(
        select(Workspace).where(
            Workspace.id == uuid.UUID(str(workspace_id)), Workspace.deleted_at.is_(None)
        )
    )
    if row is None:
        raise IdentityNotFound("workspace not found")


def _member(session, workspace_id, member_id):
    row = session.scalar(
        select(Member).where(
            Member.workspace_id == uuid.UUID(str(workspace_id)),
            Member.id == uuid.UUID(str(member_id)),
            Member.deleted_at.is_(None),
        )
    )
    if row is None:
        raise IdentityNotFound("member not found")
    return row


def _team_visible(viewer: Viewer):
    """Workspace teams are open to all; a private team only to its members and administrators."""
    is_member = (
        exists().where(
            TeamMember.workspace_id == Team.workspace_id,
            TeamMember.team_id == Team.id,
            TeamMember.member_id == viewer.member_id,
        )
        if viewer.member_id
        else false()
    )
    return or_(Team.visibility == "workspace", is_member, true() if viewer.admin else false())


def _team(session, workspace_id, team_id, viewer: Viewer):
    """A team the viewer may see; one they may not is reported as missing."""
    row = session.scalar(
        select(Team).where(
            Team.workspace_id == uuid.UUID(str(workspace_id)),
            Team.id == uuid.UUID(str(team_id)),
            Team.deleted_at.is_(None),
            _team_visible(viewer),
        )
    )
    if row is None:
        raise IdentityNotFound("team not found")
    return row


def _member_view(session, row):
    user = session.get(User, row.user_id) if row.user_id else None
    return {
        "id": row.id,
        "workspace_id": row.workspace_id,
        "user_id": row.user_id,
        "type": row.type,
        "name": user.name if user else row.name,
        "email": user.email if user else None,
        "role": row.role,
        "status": row.status,
        "agent_kind": row.agent_kind,
        "avatar_url": user.avatar_url if user else row.avatar_url,
        "settings": row.settings,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


class MemberService:
    def __init__(self, workspace_id, actor=None):
        self.workspace_id = uuid.UUID(str(workspace_id))
        self.actor = str(actor) if actor else None  # who is acting, for the audit log

    def list(self, limit=50, offset=0):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            rows = session.scalars(
                select(Member)
                .where(Member.workspace_id == self.workspace_id, Member.deleted_at.is_(None))
                .order_by(Member.created_at, Member.id)
                .limit(limit)
                .offset(offset)
            )
            return [_member_view(session, row) for row in rows]

    def get(self, member_id):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            return _member_view(session, _member(session, self.workspace_id, member_id))

    def create(self, *, type, role, status, name, email, agent_kind, avatar_url, settings):
        if type != "HUMAN" and role == "owner":
            raise ValueError("AI members cannot be owners")
        normalized_email = canonical_email(email) if email else None
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            user_id = None
            if type == "HUMAN":
                user = session.scalar(select(User).where(email_matches(normalized_email)))
                if user is None:
                    user = User(email=normalized_email, name=name.strip())
                    session.add(user)
                    session.flush()
                user_id = user.id
                duplicate = session.scalar(
                    select(Member.id).where(
                        Member.workspace_id == self.workspace_id,
                        Member.user_id == user_id,
                        Member.deleted_at.is_(None),
                    )
                )
                if duplicate:
                    raise IdentityConflict("user is already a member of this workspace")
            row = Member(
                workspace_id=self.workspace_id,
                user_id=user_id,
                type=type,
                name=name.strip() if type == "AI" else None,
                role=role,
                status=status,
                agent_kind=agent_kind,
                avatar_url=avatar_url if type == "AI" else None,
                settings=settings,
            )
            session.add(row)
            session.flush()
            events.record(session, str(self.workspace_id), self.actor, "member", row.id, "created")
            return _member_view(session, row)

    def update(self, member_id, **fields):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            guarded = "role" in fields or "status" in fields
            if guarded:
                owners = lock_owners(session, [self.workspace_id])  # first: see the latest
            row = _member(session, self.workspace_id, member_id)
            if fields.get("role") == "owner" and row.type != "HUMAN":
                raise ValueError("AI members cannot be owners")
            account = {"name", "email", "avatar_url"} & fields.keys()
            if row.type == "HUMAN" and account:
                # These live on the person's user account, shared by every workspace they are in.
                raise ValueError(
                    f"{', '.join(sorted(account))} belong to the user account, not the member"
                )
            if "email" in fields:
                raise ValueError("AI members cannot have an email")
            if "name" in fields:
                fields["name"] = fields["name"].strip()
            for key, value in fields.items():
                setattr(row, key, value)
            if guarded:
                ensure_an_owner(session, self.workspace_id, owners)
            session.flush()
            events.record(session, str(self.workspace_id), self.actor, "member", row.id, "updated")
            return _member_view(session, row)

    def delete(self, member_id):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            owners = lock_owners(session, [self.workspace_id])
            row = _member(session, self.workspace_id, member_id)
            row.deleted_at = datetime.now(UTC)
            ensure_an_owner(session, self.workspace_id, owners)
            events.record(session, str(self.workspace_id), self.actor, "member", row.id, "deleted")

    def transfer_ownership(self, to_member_id, demote_self_to=None):
        """Make another active human member an owner; optionally step the acting owner down.

        The acting member (self.actor) must be an active human owner, else IdentityForbidden.
        """
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            owners = lock_owners(session, [self.workspace_id])
            current = None
            if self.actor:
                current = session.scalar(
                    select(Member).where(
                        Member.workspace_id == self.workspace_id,
                        Member.id == uuid.UUID(self.actor),
                        Member.deleted_at.is_(None),
                    )
                )
            if current is None or not (
                current.role == "owner" and current.type == "HUMAN" and current.status == "active"
            ):
                raise IdentityForbidden("only a current owner can transfer ownership")
            target = session.scalar(
                select(Member)
                .where(
                    Member.workspace_id == self.workspace_id,
                    Member.id == uuid.UUID(str(to_member_id)),
                    Member.deleted_at.is_(None),
                )
                .with_for_update()
            )
            if target is None or target.type != "HUMAN" or target.status != "active":
                raise InvalidTarget("the new owner must be an active human member")
            if target.id == current.id:
                raise InvalidTarget("the new owner must be another member")
            target.role = "owner"
            if demote_self_to is not None:
                current.role = demote_self_to
            ensure_an_owner(session, self.workspace_id, owners)
            for changed in (target, current):
                events.record(
                    session,
                    str(self.workspace_id),
                    self.actor,
                    "member",
                    changed.id,
                    "ownership_transferred",
                    {"role": changed.role},
                )
            return {
                "workspace_id": self.workspace_id,
                "from_member": _member_view(session, current),
                "to_member": _member_view(session, target),
            }


class TeamService:
    def __init__(self, workspace_id, viewer: Viewer):
        self.workspace_id = uuid.UUID(str(workspace_id))
        self.viewer = viewer
        self.actor = str(viewer.member_id) if viewer.member_id else None

    def list(self, limit=50, offset=0):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            return list(
                session.scalars(
                    select(Team)
                    .where(
                        Team.workspace_id == self.workspace_id,
                        Team.deleted_at.is_(None),
                        _team_visible(self.viewer),
                    )
                    .order_by(Team.created_at, Team.id)
                    .limit(limit)
                    .offset(offset)
                )
            )

    def get(self, team_id):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            return _team(session, self.workspace_id, team_id, self.viewer)

    def create(self, **fields):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            row = Team(workspace_id=self.workspace_id, **fields)
            session.add(row)
            session.flush()
            events.record(session, str(self.workspace_id), self.actor, "team", row.id, "created")
            return row

    def update(self, team_id, **fields):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            row = _team(session, self.workspace_id, team_id, self.viewer)
            for key, value in fields.items():
                setattr(row, key, value)
            session.flush()
            events.record(
                session, str(self.workspace_id), self.actor, "team", row.id, "updated", fields
            )
            return row

    def delete(self, team_id):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            row = _team(session, self.workspace_id, team_id, self.viewer)
            row.deleted_at = datetime.now(UTC)
            session.flush()
            events.record(session, str(self.workspace_id), self.actor, "team", row.id, "deleted")

    def set_members(self, team_id, member_ids):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            team = _team(session, self.workspace_id, team_id, self.viewer)
            ids = {uuid.UUID(str(member_id)) for member_id in member_ids}
            active = set(
                session.scalars(
                    select(Member.id).where(
                        Member.workspace_id == self.workspace_id,
                        Member.id.in_(ids) if ids else False,
                        Member.deleted_at.is_(None),
                        Member.status == "active",
                    )
                )
            )
            if active != ids:
                raise ValueError("all team members must be active members of this workspace")
            session.execute(
                delete(TeamMember).where(
                    TeamMember.workspace_id == self.workspace_id,
                    TeamMember.team_id == team.id,
                )
            )
            session.add_all(
                TeamMember(workspace_id=self.workspace_id, team_id=team.id, member_id=member_id)
                for member_id in sorted(ids, key=str)
            )
            events.record(
                session,
                str(self.workspace_id),
                self.actor,
                "team",
                team.id,
                "members_replaced",
                {"member_ids": sorted(map(str, ids))},
            )
            return team
