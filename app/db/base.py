"""Declarative base, column mixins, and the constraint helpers every model uses.

Tenant isolation is enforced by the schema, not only by queries: every tenant table has a
workspace_id, and foreign keys between tenant tables are composite (workspace_id, x_id), so a
row can never point at another workspace's data.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    MetaData,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
ZERO_UUID = "00000000-0000-0000-0000-000000000000"
EMPTY_JSON = text("'{}'::jsonb")
EMPTY_LIST = text("'[]'::jsonb")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map = {
        uuid.UUID: UUID(as_uuid=True),
        datetime: DateTime(timezone=True),
        dict: JSONB,
        list: JSONB,
    }


class IdPk:
    # The app may supply UUIDv7 (time-ordered); gen_random_uuid() is the database fallback.
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )


class Tenant:
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))


class Created:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Stamps(Created):
    # updated_at is maintained by a trigger (see alembic/ddl.py), not by the ORM.
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class SoftDelete:
    deleted_at: Mapped[datetime | None]


def tenant_unique() -> UniqueConstraint:
    """Lets other tables reference (workspace_id, id)."""
    return UniqueConstraint("workspace_id", "id")


def project_unique() -> UniqueConstraint:
    """Lets tasks reference "a sprint/milestone/work item of the same project"."""
    return UniqueConstraint("workspace_id", "project_id", "id")


def tfk(col: str, target: str, ondelete: str | None = None) -> ForeignKeyConstraint:
    """(workspace_id, col) -> target(workspace_id, id). ondelete: 'cascade' | 'set null'."""
    action = {"cascade": "CASCADE", "set null": f"SET NULL ({col})", None: None}[ondelete]
    return ForeignKeyConstraint(
        ["workspace_id", col],
        [f"{target}.workspace_id", f"{target}.id"],
        ondelete=action,
    )


def pfk(col: str, target: str, ondelete: str | None = None) -> ForeignKeyConstraint:
    """(workspace_id, project_id, col) -> target(workspace_id, project_id, id): same project."""
    action = {"cascade": "CASCADE", "set null": f"SET NULL ({col})", None: None}[ondelete]
    return ForeignKeyConstraint(
        ["workspace_id", "project_id", col],
        [f"{target}.workspace_id", f"{target}.project_id", f"{target}.id"],
        ondelete=action,
    )


def one_of(col: str, *values: str) -> CheckConstraint:
    """Text enum as a CHECK: easy to extend with a migration."""
    listed = ", ".join(f"'{value}'" for value in values)
    return CheckConstraint(f"{col} in ({listed})", name=col)


PRIORITIES = ("none", "low", "medium", "high", "urgent")
WORK_STATUSES = ("backlog", "todo", "in_progress", "in_review", "done", "canceled")
