"""Plain CRUD functions. Each takes a session (and the org id where scoped), returns
ORM objects, raises DomainError, and commits its own transaction on writes.

Rule: a route calls at most one write CRUD function. Any operation that writes
several rows must be one CRUD function with one commit at the end."""

from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from domain.errors import not_found


def fetch_page(session: Session, stmt: Select, limit: int, offset: int) -> tuple[list, bool]:
    """limit + 1 rows are fetched to compute has_more without a COUNT query."""
    rows = session.scalars(stmt.limit(limit + 1).offset(offset)).unique().all()
    return list(rows[:limit]), len(rows) > limit


def get_or_404(session: Session, model: Any, org_id: UUID, id_: UUID, entity: str):
    obj = session.scalar(
        select(model).where(model.id == id_, model.organization_id == org_id)
    )
    if obj is None:
        raise not_found(entity)
    return obj


def save(session: Session, obj: Any):
    """Insert and commit; the refresh loads server-generated columns."""
    session.add(obj)
    session.commit()
    session.refresh(obj)
    return obj


def apply_update(session: Session, obj: Any, body: BaseModel):
    """PATCH semantics: only fields that were sent. An empty body is a no-op."""
    data = body.model_dump(exclude_unset=True)
    if not data:
        return obj
    if "archived" in data:
        obj.archived_at = func.now() if data.pop("archived") else None
    for key, value in data.items():
        setattr(obj, key, value)
    session.commit()
    session.refresh(obj)
    return obj


def archive(session: Session, obj: Any) -> None:
    obj.archived_at = func.now()
    session.commit()


def delete(session: Session, obj: Any) -> None:
    session.delete(obj)
    session.commit()
