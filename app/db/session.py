"""Engine and sessions.

Every data access goes through `session_for(workspace_id)`: it opens one transaction and tells
Postgres which workspace it belongs to, so row-level security backs up the workspace filter on
every query.

DATABASE_URL is the application's connection and must use a role WITHOUT superuser or BYPASSRLS
(a managed server's admin role usually has BYPASSRLS, which silently turns the policies off).
DATABASE_ADMIN_URL, used only to install the schema, may be that admin role. Both are read
here, not in app.config, so installing works without the API's other settings.
"""

from contextlib import contextmanager
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker


class _DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = ""
    DATABASE_ADMIN_URL: str = ""


def database_url() -> str:
    url = _DatabaseSettings().DATABASE_URL.strip()
    if not url:
        raise RuntimeError("DATABASE_URL is required, e.g. postgresql+psycopg://user:pass@host/db")
    return url


def admin_database_url() -> str:
    """Connection for installing the schema; falls back to the application's."""
    return _DatabaseSettings().DATABASE_ADMIN_URL.strip() or database_url()


@lru_cache
def engine() -> Engine:
    return create_engine(database_url(), pool_pre_ping=True, pool_size=5, max_overflow=10)


@contextmanager
def workspace_session(bind: Engine, workspace_id: str, service: bool = False):
    """A transaction scoped to one workspace, committed on success and rolled back on error.

    `service=True` lifts the workspace restriction for trusted admin work that spans workspaces.
    """
    from app.atoms.access import AtomAccessError, current_context

    atom = current_context()
    if atom and (service or str(workspace_id) != str(atom.workspace_id)):
        raise AtomAccessError("Atom context cannot cross workspaces or enable service mode")
    with sessionmaker(bind, expire_on_commit=False)() as session, session.begin():
        session.execute(
            text(
                "select set_config('app.workspace_id', :ws, true), "
                "set_config('app.service', :svc, true), "
                "set_config('app.atom_id', :atom, true), "
                "set_config('app.run_id', :run, true), "
                "set_config('app.member_id', :member, true), "
                "set_config('app.atom_aggregate', 'off', true)"
            ),
            {
                "ws": str(workspace_id),
                "svc": "on" if service else "off",
                "atom": str(atom.atom_id) if atom else "",
                "run": str(atom.run_id) if atom else "",
                "member": str(atom.member_id) if atom else "",
            },
        )
        yield session


def session_for(workspace_id: str):
    return workspace_session(engine(), workspace_id)


@contextmanager
def advisory_lock(key: str):
    """Cross-process exclusive lock. Yields True if this caller got it, False if another holds it.

    Held on its own connection for the whole block, so work inside can use other sessions.
    Postgres drops the lock if the process dies, so a crash never leaves it stuck.
    """
    with engine().connect() as conn:
        got = conn.execute(
            text("select pg_try_advisory_lock(hashtextextended(:key, 0))"), {"key": key}
        ).scalar()
        try:
            yield bool(got)
        finally:
            if got:
                conn.execute(
                    text("select pg_advisory_unlock(hashtextextended(:key, 0))"), {"key": key}
                )
            conn.commit()


def tune_vector_search(session: Session, limit: int) -> None:
    """Make HNSW return enough rows when a workspace filter is applied.

    ef_search caps how many candidates one index scan returns (default 40, max 1000).
    Iterative scans (pgvector 0.8+) keep scanning until the filtered result is full; older
    versions simply do not have the setting.
    """
    session.execute(
        text("select set_config('hnsw.ef_search', :n, true)"), {"n": str(min(max(limit, 40), 1000))}
    )
    try:
        with session.begin_nested():
            session.execute(text("select set_config('hnsw.iterative_scan', 'relaxed_order', true)"))
    except DBAPIError:
        pass
