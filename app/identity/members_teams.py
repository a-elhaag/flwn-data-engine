"""Trusted workspace member and team management."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select

from app.db import events
from app.db.models.identity import Member, Team, TeamMember, User, Workspace
from app.db.session import session_for


class IdentityNotFound(LookupError):
    pass


class IdentityConflict(ValueError):
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


def _team(session, workspace_id, team_id):
    row = session.scalar(
        select(Team).where(
            Team.workspace_id == uuid.UUID(str(workspace_id)),
            Team.id == uuid.UUID(str(team_id)),
            Team.deleted_at.is_(None),
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
    def __init__(self, workspace_id):
        self.workspace_id = uuid.UUID(str(workspace_id))

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
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            user_id = None
            normalized_email = email.strip().lower() if email else None
            if type == "HUMAN":
                user = session.scalar(
                    select(User).where(func.lower(User.email) == normalized_email)
                )
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
            events.record(session, str(self.workspace_id), None, "member", row.id, "created")
            return _member_view(session, row)

    def update(self, member_id, **fields):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            row = _member(session, self.workspace_id, member_id)
            user = session.get(User, row.user_id) if row.user_id else None
            if "name" in fields:
                if user:
                    user.name = fields.pop("name").strip()
                else:
                    row.name = fields.pop("name").strip()
            if "email" in fields:
                if user is None:
                    raise ValueError("AI members cannot have an email")
                email = fields.pop("email").strip().lower()
                duplicate = session.scalar(
                    select(User.id).where(func.lower(User.email) == email, User.id != user.id)
                )
                if duplicate:
                    raise IdentityConflict("email is already in use")
                user.email = email
            if "avatar_url" in fields:
                if user:
                    user.avatar_url = fields.pop("avatar_url")
                else:
                    row.avatar_url = fields.pop("avatar_url")
            for key, value in fields.items():
                setattr(row, key, value)
            session.flush()
            events.record(session, str(self.workspace_id), None, "member", row.id, "updated")
            return _member_view(session, row)

    def delete(self, member_id):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            row = _member(session, self.workspace_id, member_id)
            row.deleted_at = datetime.now(UTC)
            session.flush()
            events.record(session, str(self.workspace_id), None, "member", row.id, "deleted")


class TeamService:
    def __init__(self, workspace_id):
        self.workspace_id = uuid.UUID(str(workspace_id))

    def list(self, limit=50, offset=0):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            return list(
                session.scalars(
                    select(Team)
                    .where(Team.workspace_id == self.workspace_id, Team.deleted_at.is_(None))
                    .order_by(Team.created_at, Team.id)
                    .limit(limit)
                    .offset(offset)
                )
            )

    def get(self, team_id):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            return _team(session, self.workspace_id, team_id)

    def create(self, **fields):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            row = Team(workspace_id=self.workspace_id, **fields)
            session.add(row)
            session.flush()
            events.record(session, str(self.workspace_id), None, "team", row.id, "created")
            return row

    def update(self, team_id, **fields):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            row = _team(session, self.workspace_id, team_id)
            for key, value in fields.items():
                setattr(row, key, value)
            session.flush()
            events.record(session, str(self.workspace_id), None, "team", row.id, "updated", fields)
            return row

    def delete(self, team_id):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            row = _team(session, self.workspace_id, team_id)
            row.deleted_at = datetime.now(UTC)
            session.flush()
            events.record(session, str(self.workspace_id), None, "team", row.id, "deleted")

    def set_members(self, team_id, member_ids):
        with session_for(str(self.workspace_id)) as session:
            _workspace(session, self.workspace_id)
            team = _team(session, self.workspace_id, team_id)
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
                None,
                "team",
                team.id,
                "members_replaced",
                {"member_ids": sorted(map(str, ids))},
            )
            return team
