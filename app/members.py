"""Who is acting: a member exists in the workspace and is active."""

import uuid

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
