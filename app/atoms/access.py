"""Live atom identity and transaction context shared by REST, MCP and services.

Only approved, ownership-checked lifecycle operations may use privileged_atom_session.
Aggregate sessions are internal: callers must execute SQL aggregates, never return raw rows.
"""

import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from sqlalchemy import text


class AtomAccessError(PermissionError):
    pass


@dataclass(frozen=True)
class AtomContext:
    workspace_id: str
    atom_id: str
    run_id: str
    member_id: str


_context: ContextVar[AtomContext | None] = ContextVar("atom_context", default=None)


def current_context() -> AtomContext | None:
    return _context.get()


@contextmanager
def bind_context(context: AtomContext | None):
    token = _context.set(context)
    try:
        yield context
    finally:
        _context.reset(token)


def validate_context(context: AtomContext, bind=None, *, allow_finished: bool = False) -> None:
    """Validate live identity, including owner membership, even without any readable grants."""
    from app.db.session import engine, workspace_session

    if not all((context.workspace_id, context.atom_id, context.run_id, context.member_id)):
        raise AtomAccessError("Atom tokens require matching member, atom and run")
    with (
        bind_context(context),
        workspace_session(bind if bind is not None else engine(), context.workspace_id) as session,
    ):
        valid = session.scalar(
            text("select atom_context_valid(:allow_finished)"), {"allow_finished": allow_finished}
        )
    if not valid:
        raise AtomAccessError("Atom, member or run is not active or does not match")


def is_atom_member(workspace_id: str, member_id: str, bind=None) -> bool:
    from app.db.session import engine, workspace_session

    with workspace_session(bind if bind is not None else engine(), workspace_id) as session:
        return bool(
            session.scalar(
                text(
                    "select exists(select 1 from members where workspace_id = :ws "
                    "and id = :member and agent_kind = 'atom')"
                ),
                {"ws": workspace_id, "member": member_id},
            )
        )


@contextmanager
def privileged_atom_session(workspace_id: str, bind=None):
    """Internal escape hatch after the service has checked target ownership and operation.

    This never enables cross-workspace service mode. No caller-controlled argument may select
    this path; control-plane services must independently limit writable columns and objects.
    """
    from app.db.session import engine, workspace_session

    context = current_context()
    if context is not None:
        if str(workspace_id) != str(context.workspace_id):
            raise AtomAccessError("Atom context is bound to another workspace")
        validate_context(context, bind=bind)
    with (
        bind_context(None),
        workspace_session(bind if bind is not None else engine(), str(workspace_id)) as session,
    ):
        yield session


@contextmanager
def aggregate_session(context: AtomContext, bind=None):
    """Internal SQL-aggregate-only transaction with live summary grants and owner bounds."""
    from app.db.session import engine, workspace_session

    validate_context(context, bind=bind)
    with (
        bind_context(context),
        workspace_session(bind if bind is not None else engine(), context.workspace_id) as session,
    ):
        session.execute(text("select set_config('app.atom_aggregate', 'on', true)"))
        yield session


def require_row_access(session, table: str, row: dict, *, level: str = "read") -> None:
    """Service-side live authorization, including when the connection can bypass RLS."""
    context = current_context()
    if context is None:
        return
    valid = session.scalar(
        text(
            "select current_setting('app.workspace_id',true) = :ws "
            "and current_setting('app.atom_id',true) = :atom "
            "and current_setting('app.run_id',true) = :run "
            "and current_setting('app.member_id',true) = :member "
            "and atom_row_allowed(:table,cast(:row as jsonb),:level)"
        ),
        {
            "ws": context.workspace_id,
            "atom": context.atom_id,
            "run": context.run_id,
            "member": context.member_id,
            "table": table,
            "row": json.dumps(row, default=str),
            "level": level,
        },
    )
    if not valid:
        raise AtomAccessError("No live atom grant permits this operation")


def live_grants(context: AtomContext, bind=None) -> list[dict]:
    validate_context(context, bind=bind)
    from app.db.session import engine, workspace_session

    with (
        bind_context(context),
        workspace_session(bind if bind is not None else engine(), context.workspace_id) as session,
    ):
        return [
            dict(row)
            for row in session.execute(
                text(
                    "select resource_type, resource_id, level, constraints from atom_grants "
                    "where workspace_id = :ws and atom_id = :atom"
                ),
                {"ws": context.workspace_id, "atom": context.atom_id},
            ).mappings()
        ]
