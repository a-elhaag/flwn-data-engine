"""Install the database schema from scratch. There is no migration history: the database is new,
so the models are the schema.

    python -m app.db.install                        # create what is missing; safe to re-run
    python -m app.db.install --reset                # DROP every table first (dev databases only)
    APP_DB_PASSWORD=... python -m app.db.install --app-role flwn_app

Uses DATABASE_ADMIN_URL (or DATABASE_URL) from the environment or .env. Extensions and tables come
first, then the SQL in db/sql/ (functions, triggers, row-level security) in filename order.

`--app-role` creates (or updates) the least-privilege role the application connects as. Row-level
security only protects workspaces from a role that cannot bypass it; a managed server's admin role
usually can, so the application must not use it.
"""

import argparse
import re
from pathlib import Path

from psycopg import sql
from sqlalchemy import create_engine
from sqlalchemy.engine import Connection, make_url

import app.db.models  # noqa: F401  (registers every model on Base.metadata)
from app.db.base import Base
from app.db.session import admin_database_url

SQL_DIR = Path(__file__).parent / "sql"
ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def grant_app_role(conn: Connection, role: str, password: str | None = None) -> None:
    """Create the application role if missing and give it table access, nothing more.

    No password means a role that cannot log in (enough for `set role` in tests).
    """
    if not ROLE_NAME.match(role):
        raise ValueError(f"invalid role name: {role!r}")
    raw = conn.connection.driver_connection
    exists = conn.exec_driver_sql("select 1 from pg_roles where rolname = %s", (role,)).scalar()
    flags = sql.SQL("nosuperuser nobypassrls nocreatedb nocreaterole")
    login = (
        sql.SQL("login password {}").format(sql.Literal(password))
        if password
        else sql.SQL("nologin")
    )
    if not exists:
        raw.execute(sql.SQL("create role {} {} {}").format(sql.Identifier(role), login, flags))
    elif password:
        raw.execute(sql.SQL("alter role {} {} {}").format(sql.Identifier(role), login, flags))
    who = sql.Identifier(role)
    for statement in (
        "grant usage on schema public to {}",
        "grant select, insert, update, delete on all tables in schema public to {}",
        "grant usage, select on all sequences in schema public to {}",
        "alter default privileges in schema public grant select, insert, update, delete on tables to {}",
        "alter default privileges in schema public grant usage, select on sequences to {}",
    ):
        raw.execute(sql.SQL(statement).format(who))


def install(
    url: str | None = None,
    *,
    reset: bool = False,
    app_role: str | None = None,
    app_password: str | None = None,
) -> None:
    engine = create_engine(url or admin_database_url())
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("create extension if not exists vector")
            if reset:
                Base.metadata.drop_all(conn)
            Base.metadata.create_all(conn)
            # The driver runs a file of several statements only as a plain simple query.
            raw = conn.connection.driver_connection.cursor()
            for path in sorted(SQL_DIR.glob("*.sql")):
                raw.execute(path.read_text())
            if app_role:
                grant_app_role(conn, app_role, app_password)
    finally:
        engine.dispose()


if __name__ == "__main__":
    import os

    parser = argparse.ArgumentParser(description="Install the Flwn data engine schema.")
    parser.add_argument("--reset", action="store_true", help="drop all tables first")
    parser.add_argument("--app-role", help="create or update this least-privilege application role")
    args = parser.parse_args()
    target = make_url(admin_database_url())
    if args.reset:
        print(f"resetting {target.host}/{target.database}: dropping every table")
    install(
        reset=args.reset, app_role=args.app_role, app_password=os.environ.get("APP_DB_PASSWORD")
    )
    print(
        "schema installed" + (f"; application role {args.app_role} ready" if args.app_role else "")
    )
