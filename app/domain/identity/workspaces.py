"""Trusted workspace CRUD operations."""

import uuid

from sqlalchemy import func, select

from app.db import events
from app.db.models.files import BlobPurge, File
from app.db.models.identity import Member, User, Workspace
from app.db.session import service_session
from app.domain.error_codes import DomainError, InvalidReference


class WorkspaceNotFound(DomainError, LookupError):
    code = "workspace_not_found"


class WorkspaceConflict(DomainError, ValueError):
    code = "workspace_conflict"


def _workspace(session, workspace_id):
    row = session.scalar(select(Workspace).where(Workspace.id == uuid.UUID(str(workspace_id))))
    if row is None:
        raise WorkspaceNotFound("workspace not found", code="workspace_not_found")
    return row


def _slug_available(session, slug, *, exclude_id=None):
    query = select(Workspace.id).where(func.lower(Workspace.slug) == slug.lower())
    if exclude_id is not None:
        query = query.where(Workspace.id != exclude_id)
    if session.scalar(query) is not None:
        raise WorkspaceConflict("workspace slug already exists", code="workspace_slug_taken")


class WorkspaceService:
    def __init__(self, actor=None):
        self.actor = str(actor) if actor else None  # who is acting, for the audit log

    def list(self, limit=50, offset=0):
        with service_session() as session:
            rows = session.scalars(
                select(Workspace)
                .order_by(Workspace.created_at, Workspace.id)
                .limit(limit)
                .offset(offset)
            )
            return list(rows)

    def get(self, workspace_id):
        with service_session() as session:
            return _workspace(session, workspace_id)

    def create(self, *, name, slug, owner_user_id, description=None, settings=None):
        """The workspace and its first owner (an active HUMAN member), in one transaction."""
        owner = uuid.UUID(str(owner_user_id))
        with service_session() as session:
            user = session.scalar(select(User.id).where(User.id == owner))
            if user is None:
                raise InvalidReference("owner_user")
            _slug_available(session, slug)
            row = Workspace(
                name=name.strip(),
                slug=slug,
                description=description,
                settings=settings or {},
                created_by=owner,
            )
            session.add(row)
            session.flush()
            member = Member(
                workspace_id=row.id, user_id=owner, type="HUMAN", role="owner", status="active"
            )
            session.add(member)
            session.flush()
            events.record(session, str(row.id), None, "workspace", row.id, "created")
            events.record(session, str(row.id), None, "member", member.id, "created")
            return row

    def update(self, workspace_id, **fields):
        with service_session() as session:
            row = _workspace(session, workspace_id)
            if "slug" in fields:
                _slug_available(session, fields["slug"], exclude_id=row.id)
            if "name" in fields:
                fields["name"] = fields["name"].strip()
            for key, value in fields.items():
                setattr(row, key, value)
            session.flush()
            events.record(session, str(row.id), self.actor, "workspace", row.id, "updated", fields)
            return row

    def delete(self, workspace_id):
        with service_session() as session:
            row = _workspace(session, workspace_id)
            # The bytes cannot be deleted inside this transaction: record each blob for a reaper
            # (blob_purges), in the same commit that removes the rows.
            for container, blob_path in session.execute(
                select(File.container, File.blob_path).where(File.workspace_id == row.id)
            ).all():
                session.add(
                    BlobPurge(workspace_id=row.id, container=container, blob_path=blob_path)
                )
            # Everything inside the workspace goes with it (ON DELETE CASCADE), its events too.
            session.delete(row)
