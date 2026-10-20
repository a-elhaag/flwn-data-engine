"""Who is acting: a member exists in the workspace and is active."""

import uuid
from dataclasses import dataclass

from sqlalchemy import select

from app.db.models.identity import Member
from app.db.session import session_for


def is_active(workspace_id: str, member_id: str) -> bool:
    """True if the member belongs to the workspace and is neither suspended nor deleted."""
    with session_for(workspace_id) as session:
        found = session.scalar(
            select(Member.id).where(
                Member.workspace_id == uuid.UUID(workspace_id),
                Member.id == uuid.UUID(member_id),
                Member.status == "active",
                Member.deleted_at.is_(None),
            )
        )
        return found is not None


def user_id(workspace_id: str, member_id: str) -> str | None:
    """The user account behind a human member; None for an AI member."""
    with session_for(workspace_id) as session:
        found = session.scalar(
            select(Member.user_id).where(
                Member.workspace_id == uuid.UUID(workspace_id),
                Member.id == uuid.UUID(member_id),
            )
        )
        return str(found) if found else None


@dataclass(frozen=True)
class Viewer:
    """Who is looking, for the services that hide things from some members."""

    member_id: uuid.UUID | None
    admin: bool
