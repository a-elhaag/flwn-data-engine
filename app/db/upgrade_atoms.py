"""Opt-in, additive upgrade of pre-Atoms and existing Atoms databases, including approvals.

    python -m app.db.upgrade_atoms --app-role flwn_app

Requires DATABASE_ADMIN_URL; never falls back to DATABASE_URL. All DDL, installation SQL,
and grants commit together or roll back together. Existing role credentials and
login flags are never changed. Roll back the application image, not this schema;
keep the additive tables/columns and their data. Use app.db.install for an empty DB.
"""

import argparse
import re

from psycopg import sql
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, create_engine, inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.schema import AddConstraint, CreateColumn

from app.db.base import Base
from app.db.install import ROLE_NAME, SQL_DIR, grant_privileges
from app.db.session import admin_database_url

ATOM_TABLES = (
    "atoms",
    "atom_versions",
    "skills",
    "catalog_skills",
    "atom_skills",
    "atom_schedules",
    "atom_connections",
    "atom_grants",
)
RUN_COLUMNS = (
    "atom_id",
    "atom_version_id",
    "schedule_id",
    "idempotency_key",
    "tokens_in",
    "tokens_out",
    "cost",
)
APPROVAL_COLUMNS = (
    "atom_id",
    "title",
    "request_key",
    "execution_run_id",
    "claimed_at",
    "executed_at",
    "execution_status",
    "execution_result",
)
RUN_CONSTRAINTS = (
    "uq_agent_runs_workspace_id_idempotency_key",
    "ck_agent_runs_atom_schedule_only",
    "ck_agent_runs_nonnegative_usage",
    "fk_agent_runs_workspace_id_atom_id_atoms",
    "fk_agent_runs_workspace_id_atom_version_id_atom_versions",
    "fk_agent_runs_workspace_id_schedule_id_atom_schedules",
)
WIDENED_CHECKS = {
    "members": "ck_members_agent_kind",
    "approvals": "ck_approvals_kind",
}


def _check_roles(conn: Connection, app_role: str) -> None:
    if not ROLE_NAME.fullmatch(app_role):
        raise ValueError(f"invalid role name: {app_role!r}")
    runtime = conn.execute(
        text("select rolsuper, rolbypassrls from pg_roles where rolname = :role"),
        {"role": app_role},
    ).first()
    if runtime is None or runtime.rolsuper or runtime.rolbypassrls:
        raise RuntimeError("app role must already exist and have neither SUPERUSER nor BYPASSRLS")
    # FORCE RLS also applies to table owners. Recursive SECURITY DEFINER helpers
    # need a genuine bypass; app.service='on' does not bypass restrictive policies.
    if not conn.exec_driver_sql(
        "select rolsuper or rolbypassrls from pg_roles where rolname = current_user"
    ).scalar_one():
        raise RuntimeError("upgrade/helper owner requires SUPERUSER or BYPASSRLS; no roles changed")
    unsafe = (
        conn.exec_driver_sql(
            "select p.oid::regprocedure::text from pg_proc p "
            "join pg_namespace n on n.oid = p.pronamespace "
            "join pg_roles r on r.oid = p.proowner "
            "where n.nspname = 'public' and starts_with(p.proname, 'atom_') "
            "and p.prosecdef and not (r.rolsuper or r.rolbypassrls)"
        )
        .scalars()
        .all()
    )
    if unsafe:
        raise RuntimeError(f"existing Atom helper owners cannot bypass forced RLS: {unsafe}")


def _check_helper_access(conn: Connection) -> None:
    # CREATE OR REPLACE preserves an existing function's owner. BYPASSRLS alone
    # does not confer table privileges, so check those owners too without SET ROLE.
    missing = (
        conn.exec_driver_sql(
            "select distinct r.rolname from pg_proc p "
            "join pg_namespace n on n.oid = p.pronamespace "
            "join pg_roles r on r.oid = p.proowner "
            "where n.nspname = 'public' and starts_with(p.proname, 'atom_') and p.prosecdef "
            "and (not has_schema_privilege(r.oid, 'public', 'USAGE') or exists ("
            "select 1 from pg_class t join pg_namespace s on s.oid = t.relnamespace "
            "where s.nspname = 'public' and t.relkind in ('r', 'p') "
            "and not has_table_privilege(r.oid, t.oid, 'SELECT')) "
            "or not has_table_privilege(r.oid, 'public.events', 'INSERT'))"
        )
        .scalars()
        .all()
    )
    if missing:
        raise RuntimeError(f"Atom helper owners lack required table access: {missing}")


def _grant_existing_app_role(conn: Connection, role: str) -> None:
    # Deliberately not the installer's role-creation/password-management path.
    grant_privileges(conn.connection.driver_connection, role)


def _replace_check(conn: Connection, constraint: CheckConstraint) -> None:
    conn.connection.driver_connection.execute(
        sql.SQL("alter table public.{} drop constraint {}").format(
            sql.Identifier(constraint.table.name), sql.Identifier(constraint.name)
        )
    )
    conn.execute(AddConstraint(constraint, isolate_from_table=False))


def _check_legacy_shape(conn: Connection) -> None:
    inspector = inspect(conn)
    for table_name in ("members", "approvals", "agent_runs"):
        table = Base.metadata.tables[table_name]
        columns = {c["name"]: c for c in inspector.get_columns(table_name, schema="public")}
        for column in table.c:
            actual = columns.get(column.name)
            if actual is None and (
                (table_name == "agent_runs" and column.name in RUN_COLUMNS)
                or (table_name == "approvals" and column.name in APPROVAL_COLUMNS)
            ):
                continue
            if actual is None or (
                actual["nullable"] != column.nullable
                or str(actual["type"].compile(dialect=conn.dialect))
                != str(column.type.compile(dialect=conn.dialect))
            ):
                raise RuntimeError(
                    f"unexpected schema for {table_name}.{column.name}; no upgrade applied"
                )
            if table_name == "agent_runs" and column.name in ("tokens_in", "tokens_out", "cost"):
                if not re.fullmatch(r"'?0'?(?:::(?:integer|numeric))?", actual["default"] or ""):
                    raise RuntimeError(f"unexpected default for agent_runs.{column.name}")
        if (name := WIDENED_CHECKS.get(table_name)) and name not in {
            c["name"] for c in inspector.get_check_constraints(table_name, schema="public")
        }:
            raise RuntimeError(f"expected legacy constraint {name}; no upgrade applied")


def _check_existing_run_constraint(conn: Connection, constraint) -> None:
    inspector = inspect(conn)
    table_name = constraint.table.name
    if isinstance(constraint, ForeignKeyConstraint):
        actual = next(
            (
                c
                for c in inspector.get_foreign_keys(table_name, schema="public")
                if c["name"] == constraint.name
            ),
            None,
        )
        if actual and (
            actual["constrained_columns"] == list(constraint.columns.keys())
            and actual["referred_table"] == constraint.referred_table.name
            and actual["referred_columns"] == [e.column.name for e in constraint.elements]
            and actual["referred_schema"] in (None, "public")
            and actual["options"].get("ondelete") == constraint.ondelete
            and not actual["options"].get("deferrable", False)
        ):
            return
    else:
        actual = next(
            (
                c
                for c in inspector.get_unique_constraints(table_name, schema="public")
                if c["name"] == constraint.name
            ),
            None,
        )
        if actual and actual["column_names"] == list(constraint.columns.keys()):
            return
    raise RuntimeError(f"unexpected definition for {constraint.name}; no upgrade applied")


def _add_missing_columns(conn: Connection, table_name: str, names) -> None:
    table = Base.metadata.tables[table_name]
    present = {c["name"] for c in inspect(conn).get_columns(table_name, schema="public")}
    for name in names:
        if name not in present:
            definition = str(CreateColumn(table.c[name]).compile(dialect=conn.dialect))
            conn.exec_driver_sql(f"alter table public.{table_name} add column {definition}")


def _sync_constraints(conn: Connection, table, wanted) -> None:
    """Add the constraints `wanted` selects; widen existing checks and verify existing keys."""
    present = set(
        conn.exec_driver_sql(
            f"select conname from pg_constraint where conrelid = 'public.{table.name}'::regclass"
        ).scalars()
    )
    for constraint in sorted(table.constraints, key=lambda c: c.name):
        if not wanted(constraint):
            continue
        if constraint.name not in present:
            conn.execute(AddConstraint(constraint, isolate_from_table=False))
        elif isinstance(constraint, CheckConstraint):
            _replace_check(conn, constraint)
        else:
            _check_existing_run_constraint(conn, constraint)
            if isinstance(constraint, ForeignKeyConstraint):
                conn.connection.driver_connection.execute(
                    sql.SQL("alter table public.{} validate constraint {}").format(
                        sql.Identifier(table.name), sql.Identifier(constraint.name)
                    )
                )


def _normalize_where(value) -> str:
    return re.sub(r"::text|[\s()]", "", ("" if value is None else str(value)).lower())


def _ensure_index(conn: Connection, table, index_name: str) -> None:
    """Create the index, or fail if an index of that name exists with a different shape."""
    index = next(i for i in table.indexes if i.name == index_name)
    existing = next(
        (
            i
            for i in inspect(conn).get_indexes(table.name, schema="public")
            if i["name"] == index.name
        ),
        None,
    )
    if existing:
        options = existing.get("dialect_options", {})
        if (
            existing["column_names"] != list(index.columns.keys())
            or existing["unique"]
            or _normalize_where(options.get("postgresql_where"))
            != _normalize_where(index.dialect_options["postgresql"]["where"])
            or any(value for key, value in options.items() if key != "postgresql_where")
        ):
            raise RuntimeError(f"unexpected definition for {index.name}; no upgrade applied")
    index.create(conn, checkfirst=True)


def _wants_approval_constraint(constraint) -> bool:
    return bool(set(constraint.columns.keys()) & set(APPROVAL_COLUMNS)) or constraint.name in (
        "ck_approvals_atom_payload",
        "ck_approvals_atom_execution",
        "ck_approvals_execution_status",
    )


def _upgrade_approvals(conn: Connection) -> None:
    _add_missing_columns(conn, "approvals", APPROVAL_COLUMNS)
    _sync_constraints(conn, Base.metadata.tables["approvals"], _wants_approval_constraint)
    _ensure_index(conn, Base.metadata.tables["approvals"], "ix_approvals_atom_ready")


def _upgrade_runs(conn: Connection) -> None:
    _add_missing_columns(conn, "agent_runs", RUN_COLUMNS)
    # The new tables below must exist before the run constraints that reference them.
    Base.metadata.create_all(conn, tables=[Base.metadata.tables[n] for n in ATOM_TABLES])
    _upgrade_approvals(conn)
    for table_name, name in WIDENED_CHECKS.items():
        constraint = next(c for c in Base.metadata.tables[table_name].constraints if c.name == name)
        _replace_check(conn, constraint)
    runs = Base.metadata.tables["agent_runs"]
    _sync_constraints(conn, runs, lambda constraint: constraint.name in RUN_CONSTRAINTS)
    _ensure_index(conn, runs, "ix_agent_runs_atom_created")


def upgrade_atoms(url: str | None = None, *, app_role: str) -> None:
    """Upgrade an installed legacy/current schema in one data-preserving transaction."""
    engine = create_engine(url or admin_database_url())
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("set local search_path to public")
            conn.exec_driver_sql("set local lock_timeout = '10s'")
            conn.exec_driver_sql("set local statement_timeout = '5min'")
            # Serialize this CLI's retries without holding an unbounded advisory wait.
            if not conn.exec_driver_sql("select pg_try_advisory_xact_lock(6813, 399)").scalar_one():
                raise RuntimeError("another Atoms upgrade is running; retry later")
            _check_roles(conn, app_role)
            existing = set(inspect(conn).get_table_names(schema="public"))
            legacy = set(Base.metadata.tables) - set(ATOM_TABLES)
            if missing := legacy - existing:
                raise RuntimeError(
                    f"expected installed pre-Atoms schema; missing tables: {sorted(missing)}"
                )

            _check_legacy_shape(conn)
            _upgrade_runs(conn)

            # Columns and referenced tables must exist before functions/triggers/RLS.
            for path in sorted(SQL_DIR.glob("*.sql")):
                conn.connection.driver_connection.execute(path.read_text())
            _check_roles(conn, app_role)
            _check_helper_access(conn)
            _grant_existing_app_role(conn, app_role)
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Transactionally upgrade an installed schema with Atoms and parked approvals."
    )
    parser.add_argument(
        "--app-role", required=True, help="existing non-bypass runtime role to grant access"
    )
    args = parser.parse_args()
    upgrade_atoms(app_role=args.app_role)
    print("Atoms upgrade committed; existing data and runtime credentials preserved")


if __name__ == "__main__":
    main()
