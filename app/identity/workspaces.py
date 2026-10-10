"""Trusted workspace CRUD operations."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.db import events
from app.db.models.identity import Workspace
from app.db.session import engine, workspace_session


class WorkspaceNotFound(LookupError):
    pass


class WorkspaceConflict(ValueError):
    pass


def _workspace(session, workspace_id):
    row = session.scalar(
        select(Workspace).where(
            Workspace.id == uuid.UUID(str(workspace_id)), Workspace.deleted_at.is_(None)
        )
    )
    if row is None:
        raise WorkspaceNotFound("workspace not found")
    return row


def _slug_available(session, slug, *, exclude_id=None):
    query = select(Workspace.id).where(
        func.lower(Workspace.slug) == slug.lower(), Workspace.deleted_at.is_(None)
    )
    if exclude_id is not None:
        query = query.where(Workspace.id != exclude_id)
    if session.scalar(query) is not None:
        raise WorkspaceConflict("workspace slug already exists")


class WorkspaceService:
    def list(self, limit=50, offset=0):
        with workspace_session(engine(), "", service=True) as session:
            rows = session.scalars(
                select(Workspace)
                .where(Workspace.deleted_at.is_(None))
                .order_by(Workspace.created_at, Workspace.id)
                .limit(limit)
                .offset(offset)
            )
            return list(rows)

    def get(self, workspace_id):
        with workspace_session(engine(), "", service=True) as session:
            return _workspace(session, workspace_id)

    def create(self, *, name, slug, description=None, settings=None):
        with workspace_session(engine(), "", service=True) as session:
            _slug_available(session, slug)
            row = Workspace(
                name=name.strip(),
                slug=slug,
                description=description,
                settings=settings or {},
            )
            session.add(row)
            session.flush()
            events.record(session, str(row.id), None, "workspace", row.id, "created")
            return row

    def update(self, workspace_id, **fields):
        with workspace_session(engine(), "", service=True) as session:
            row = _workspace(session, workspace_id)
            if "slug" in fields:
                _slug_available(session, fields["slug"], exclude_id=row.id)
            if "name" in fields:
                fields["name"] = fields["name"].strip()
            for key, value in fields.items():
                setattr(row, key, value)
            session.flush()
            events.record(session, str(row.id), None, "workspace", row.id, "updated", fields)
            return row

    def delete(self, workspace_id):
        with workspace_session(engine(), "", service=True) as session:
            row = _workspace(session, workspace_id)
            row.deleted_at = datetime.now(UTC)
            session.flush()
            events.record(session, str(row.id), None, "workspace", row.id, "deleted")
