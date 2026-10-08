"""The audit log: who did what to which thing. Rows are only ever added (a trigger blocks updates)."""

import uuid

from sqlalchemy.orm import Session

from app.db.models.memory import Event


def record(
    session: Session,
    workspace_id: str,
    actor: str | None,
    entity_type: str,
    entity_id: str | uuid.UUID | None,
    action: str,
    changes: dict | None = None,
) -> None:
    """Add an event in the caller's transaction, so it commits or rolls back with the change."""
    session.add(
        Event(
            workspace_id=uuid.UUID(workspace_id),
            actor_id=uuid.UUID(actor) if actor else None,
            entity_type=entity_type,
            entity_id=uuid.UUID(str(entity_id)) if entity_id else None,
            action=action,
            changes=changes or {},
        )
    )
