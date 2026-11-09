"""The last-owner rule: every live workspace keeps at least one active human owner.

A change that can remove an owner (role, status, delete, user delete, transfer) first locks all of
the workspace's owner rows with SELECT ... FOR UPDATE, in id order, inside its own transaction.
Two such changes on one workspace therefore run one after the other, and the second sees the first
when it counts. The deferred trigger in db/sql/70_identity.sql backs this up for other writers.
"""

import uuid

from sqlalchemy import func, select

from app.db.models.identity import Member, User, Workspace
from app.domain.error_codes import DomainError

LAST_OWNER = "last_owner"


class LastOwner(DomainError):
    """The change would leave a workspace with no active human owner (HTTP 409 last_owner)."""

    code = "last_owner"

    def __init__(self):
        super().__init__(LAST_OWNER)


def lock_owners(session, workspace_ids) -> dict:
    """Lock every owner row of these workspaces until the transaction ends.

    Returns each workspace's active owner count at that moment, for ensure_an_owner.
    """
    ids = sorted({uuid.UUID(str(ws)) for ws in workspace_ids}, key=str)
    if ids:
        session.execute(
            select(Member.id)
            .where(
                Member.workspace_id.in_(ids), Member.role == "owner", Member.deleted_at.is_(None)
            )
            .order_by(Member.workspace_id, Member.id)
            .with_for_update()
        ).all()
    return {ws: active_owners(session, ws) for ws in ids}


def active_owners(session, workspace_id, *, excluding_user=None) -> int:
    """Active HUMAN owners whose user account is live."""
    query = (
        select(func.count())
        .select_from(Member)
        .join(User, User.id == Member.user_id)
        .where(
            Member.workspace_id == uuid.UUID(str(workspace_id)),
            Member.role == "owner",
            Member.type == "HUMAN",
            Member.status == "active",
            Member.deleted_at.is_(None),
            User.deleted_at.is_(None),
        )
    )
    if excluding_user is not None:
        query = query.where(Member.user_id != uuid.UUID(str(excluding_user)))
    return session.scalar(query)


def ensure_an_owner(session, workspace_id, before: dict) -> None:
    """Raise LastOwner if the (flushed) change took a live workspace's last active owner.

    `before` is what lock_owners returned. A workspace that already had none (legacy data) is
    not blocked by changes that do not add one.
    """
    workspace_id = uuid.UUID(str(workspace_id))
    session.flush()
    live = session.scalar(
        select(Workspace.id).where(Workspace.id == workspace_id, Workspace.deleted_at.is_(None))
    )
    if (
        live is not None
        and before.get(workspace_id, 0) > 0
        and not active_owners(session, workspace_id)
    ):
        raise LastOwner()
