"""Data-preserving upgrades of the 3b6ac80 schema on disposable PostgreSQL."""

import unittest
import uuid
from unittest.mock import patch

import db_support
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from psycopg import sql
from sqlalchemy import CheckConstraint, MetaData, create_engine, inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.schema import conv
from test_atoms_schema import make_atom, make_version
from test_schema import add, make_workspace

from app.db.base import Base
from app.db.install import SQL_DIR, grant_app_role, install
from app.db.upgrade_atoms import _grant_existing_app_role, upgrade_atoms

# The complete model delta from 3b6ac80, kept independent of the upgrade's lists
# so omission of a migrated column/constraint/table is caught by schema comparison.
NEW_TABLES = {
    "atoms",
    "atom_versions",
    "atom_schedules",
    "atom_connections",
    "atom_grants",
    "skills",
    "catalog_skills",
    "atom_skills",
}
NEW_RUN_COLUMNS = {
    "atom_id",
    "atom_version_id",
    "schedule_id",
    "idempotency_key",
    "tokens_in",
    "tokens_out",
    "cost",
}


def legacy_metadata():
    metadata = MetaData(naming_convention=Base.metadata.naming_convention)
    for table in Base.metadata.tables.values():
        if table.name not in NEW_TABLES:
            table.to_metadata(metadata)
    runs = metadata.tables["agent_runs"]
    for constraint in list(runs.constraints):
        if set(constraint.columns.keys()) & NEW_RUN_COLUMNS or constraint.name in {
            "ck_agent_runs_atom_schedule_only",
            "ck_agent_runs_nonnegative_usage",
        }:
            runs.constraints.remove(constraint)
            for fk in getattr(constraint, "elements", ()):
                runs.foreign_keys.remove(fk)
    for index in list(runs.indexes):
        if set(index.columns.keys()) & NEW_RUN_COLUMNS:
            runs.indexes.remove(index)
    for name in NEW_RUN_COLUMNS:
        runs._columns.remove(runs.c[name])
    for table, name, expression in (
        (
            "members",
            "ck_members_agent_kind",
            "agent_kind is null or agent_kind in ('team_agent', 'ghost_engineer', 'decision_ledger', "
            "'memory_steward', 'scrum_master', 'security', 'architecture', 'behavioral')",
        ),
        ("approvals", "ck_approvals_kind", "kind in ('plan', 'pull_request', 'deploy', 'action')"),
    ):
        target = metadata.tables[table]
        target.constraints.remove(next(c for c in target.constraints if c.name == name))
        target.append_constraint(CheckConstraint(expression, name=conv(name)))
    return metadata


class AtomsUpgradeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = db_support.engine()
        cls.admin = create_engine(source.url, isolation_level="AUTOCOMMIT")

    @classmethod
    def tearDownClass(cls):
        cls.admin.dispose()

    def setUp(self):
        self.name = f"atoms_upgrade_{uuid.uuid4().hex[:10]}"
        self.role = f"atoms_runtime_{uuid.uuid4().hex[:10]}"
        with self.admin.connect() as conn:
            conn.connection.driver_connection.execute(
                sql.SQL("create database {}").format(sql.Identifier(self.name))
            )
            conn.connection.driver_connection.execute(
                sql.SQL("create role {} login password {} nosuperuser nobypassrls").format(
                    sql.Identifier(self.role), sql.Literal(uuid.uuid4().hex)
                )
            )
        self.url = self.admin.url.set(database=self.name).render_as_string(hide_password=False)
        self.engine = create_engine(self.url)
        self.legacy = legacy_metadata()
        with self.engine.begin() as conn:
            conn.exec_driver_sql("create extension if not exists vector")
            self.legacy.create_all(conn)
            for path in sorted(SQL_DIR.glob("*.sql")):
                if path.name < "30_atoms.sql":
                    conn.connection.driver_connection.execute(path.read_text())
            grant_app_role(conn, self.role)
            self.ws = make_workspace(conn, "legacy")
            self.other_ws = make_workspace(conn, "other")
            self.run = conn.execute(
                self.legacy.tables["agent_runs"]
                .insert()
                .values(
                    workspace_id=self.ws["workspace_id"],
                    agent_id=self.ws["agent"],
                    trigger="chat",
                    input={"legacy": True},
                    output={"keep": "result"},
                )
                .returning(self.legacy.tables["agent_runs"].c.id)
            ).scalar_one()
            self.approval = add(
                conn,
                "approvals",
                workspace_id=self.ws["workspace_id"],
                run_id=self.run,
                kind="action",
                payload={"keep": [1, 2]},
            )
            self.rows = {
                name: conn.execute(table.select()).all()
                for name, table in self.legacy.tables.items()
            }
            self.credentials = self.role_state(conn)

    def tearDown(self):
        self.engine.dispose()
        with self.admin.connect() as conn:
            conn.connection.driver_connection.execute(
                sql.SQL("drop database {} with (force)").format(sql.Identifier(self.name))
            )
            conn.connection.driver_connection.execute(
                sql.SQL("drop role {}").format(sql.Identifier(self.role))
            )

    def role_state(self, conn):
        return conn.execute(
            text(
                "select rolcanlogin, rolpassword, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole "
                "from pg_authid where rolname = :role"
            ),
            {"role": self.role},
        ).one()

    def assert_legacy_preserved(self, conn):
        for name, table in self.legacy.tables.items():
            self.assertCountEqual(conn.execute(table.select()).all(), self.rows[name], name)
        self.assertEqual(self.role_state(conn), self.credentials)

    def assert_current_schema(self, conn):
        self.assertEqual(compare_metadata(MigrationContext.configure(conn), Base.metadata), [])
        expected_checks = {
            (t.name, c.name)
            for t in Base.metadata.tables.values()
            for c in t.constraints
            if isinstance(c, CheckConstraint)
        }
        checks = set(
            conn.exec_driver_sql(
                "select c.relname, k.conname from pg_constraint k join pg_class c on c.oid=k.conrelid "
                "join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and k.contype='c'"
            ).all()
        )
        self.assertEqual(checks, expected_checks)

    def test_upgrade_preserves_all_legacy_rows_and_matches_fresh_schema(self):
        upgrade_atoms(self.url, app_role=self.role)
        with self.engine.begin() as conn:
            self.assert_legacy_preserved(conn)
            self.assert_current_schema(conn)
            row = conn.exec_driver_sql(
                "select atom_id, atom_version_id, schedule_id, idempotency_key, tokens_in, tokens_out, cost "
                "from agent_runs where id = %s",
                (self.run,),
            ).one()
            self.assertEqual(tuple(row), (None, None, None, None, 0, 0, 0))
            self.assertTrue(
                conn.exec_driver_sql(
                    "select bool_and(r.rolname = current_user and (r.rolsuper or r.rolbypassrls)) "
                    "from pg_proc p join pg_roles r on r.oid=p.proowner join pg_namespace n on n.oid=p.pronamespace "
                    "where n.nspname='public' and starts_with(p.proname, 'atom_') and p.prosecdef"
                ).scalar_one()
            )

    def test_old_app_reads_and_writes_with_only_old_session_settings(self):
        upgrade_atoms(self.url, app_role=self.role)
        with self.engine.begin() as conn:
            conn.connection.driver_connection.execute(
                sql.SQL("set local role {}").format(sql.Identifier(self.role))
            )
            conn.execute(
                text(
                    "select set_config('app.workspace_id', :ws, true), "
                    "set_config('app.service', 'off', true)"
                ),
                {"ws": str(self.ws["workspace_id"])},
            )
            self.assertEqual(
                conn.execute(self.legacy.tables["agent_runs"].select()).one().id, self.run
            )
            self.assertEqual(
                conn.exec_driver_sql("select count(*) from workspaces").scalar_one(), 1
            )
            conn.execute(
                self.legacy.tables["agent_runs"]
                .insert()
                .values(
                    workspace_id=self.ws["workspace_id"],
                    agent_id=self.ws["agent"],
                    trigger="user",
                )
            )
            add(conn, "approvals", workspace_id=self.ws["workspace_id"], kind="deploy")
            atom = make_atom(conn, self.ws)
            make_version(conn, atom)
            add(conn, "approvals", workspace_id=self.ws["workspace_id"], kind="atom_action")
            for name in NEW_TABLES:
                self.assertTrue(
                    conn.execute(
                        text(
                            "select has_table_privilege(current_user, :table, 'SELECT,INSERT,UPDATE,DELETE')"
                        ),
                        {"table": f"public.{name}"},
                    ).scalar_one(),
                    name,
                )

    def test_idempotent_rerun_keeps_atom_data_and_credentials(self):
        upgrade_atoms(self.url, app_role=self.role)
        with self.engine.begin() as conn:
            atom = make_atom(conn, self.ws)
            version = make_version(conn, atom)
            run = add(
                conn,
                "agent_runs",
                workspace_id=self.ws["workspace_id"],
                agent_id=atom["member_id"],
                atom_id=atom["id"],
                atom_version_id=version,
                trigger="schedule",
                idempotency_key="retry:1",
                tokens_in=7,
                cost="0.03",
            )
            snapshot = conn.exec_driver_sql(
                "select to_jsonb(r) from agent_runs r where id=%s", (run,)
            ).scalar_one()
        with patch.dict("os.environ", {"APP_DB_PASSWORD": "must-not-be-used"}):
            upgrade_atoms(self.url, app_role=self.role)
        with self.engine.connect() as conn:
            self.assert_current_schema(conn)
            self.assertEqual(self.role_state(conn), self.credentials)
            self.assertEqual(
                conn.exec_driver_sql(
                    "select to_jsonb(r) from agent_runs r where id=%s", (run,)
                ).scalar_one(),
                snapshot,
            )
            self.assertEqual(
                conn.exec_driver_sql("select id from atom_versions").scalar_one(), version
            )

    def test_normal_fresh_install_is_unchanged(self):
        # Empty only this disposable database; exercise the normal installer, not --reset.
        with self.engine.begin() as conn:
            self.legacy.drop_all(conn)
        install(self.url, app_role=self.role)
        install(self.url, app_role=self.role)
        upgrade_atoms(self.url, app_role=self.role)
        with self.engine.begin() as conn:
            self.assert_current_schema(conn)
            ws = make_workspace(conn, "fresh")
            make_version(conn, make_atom(conn, ws))
            self.assertEqual(self.role_state(conn), self.credentials)

    def test_late_failure_rolls_back_ddl_sql_grants_and_preserves_rows(self):
        def fail_after_grants(conn, role):
            _grant_existing_app_role(conn, role)
            self.assertIn("atoms", inspect(conn).get_table_names())
            conn.exec_driver_sql("select 1 / 0")

        with patch("app.db.upgrade_atoms._grant_existing_app_role", side_effect=fail_after_grants):
            with self.assertRaises(DBAPIError):
                upgrade_atoms(self.url, app_role=self.role)
        with self.engine.connect() as conn:
            self.assert_legacy_preserved(conn)
            self.assertEqual(compare_metadata(MigrationContext.configure(conn), self.legacy), [])
            self.assertFalse(
                conn.exec_driver_sql(
                    "select exists(select 1 from pg_proc p join pg_namespace n on n.oid=p.pronamespace "
                    "where n.nspname='public' and starts_with(p.proname, 'atom_'))"
                ).scalar_one()
            )
            checks = {
                c["name"]: c["sqltext"] for c in inspect(conn).get_check_constraints("members")
            }
            self.assertNotIn("'atom'", checks["ck_members_agent_kind"])
            self.assertNotIn("atom_action", str(inspect(conn).get_check_constraints("approvals")))
        upgrade_atoms(self.url, app_role=self.role)

    def test_usage_idempotency_and_atom_run_constraints_are_enforced(self):
        upgrade_atoms(self.url, app_role=self.role)
        with self.engine.begin() as conn:
            atom = make_atom(conn, self.ws)
            version = make_version(conn, atom)
            values = dict(
                workspace_id=self.ws["workspace_id"],
                agent_id=atom["member_id"],
                atom_id=atom["id"],
                atom_version_id=version,
                trigger="schedule",
            )
            for invalid in (
                {"tokens_in": -1},
                {"tokens_out": -1},
                {"cost": -1},
                {"cost": "NaN"},
                {"trigger": "chat"},
                {"atom_id": uuid.uuid4()},
                {"atom_version_id": uuid.uuid4()},
                {"schedule_id": uuid.uuid4()},
            ):
                with self.subTest(invalid=invalid), self.assertRaises(DBAPIError):
                    with conn.begin_nested():
                        add(conn, "agent_runs", **(values | invalid))
            add(conn, "agent_runs", **values, idempotency_key="once")
            with self.assertRaises(DBAPIError), conn.begin_nested():
                add(conn, "agent_runs", **values, idempotency_key="once")

    def test_partial_column_or_constraint_drift_fails_closed(self):
        with self.engine.begin() as conn:
            conn.exec_driver_sql("alter table agent_runs add column tokens_in text")
        with self.assertRaisesRegex(RuntimeError, "agent_runs.tokens_in"):
            upgrade_atoms(self.url, app_role=self.role)
        with self.engine.connect() as conn:
            self.assert_legacy_preserved(conn)
            self.assertNotIn("atoms", inspect(conn).get_table_names())
        with self.engine.begin() as conn:
            conn.exec_driver_sql("alter table agent_runs drop column tokens_in")
        upgrade_atoms(self.url, app_role=self.role)
        with self.engine.begin() as conn:
            conn.exec_driver_sql(
                "alter table agent_runs drop constraint fk_agent_runs_workspace_id_atom_id_atoms"
            )
            conn.exec_driver_sql(
                "alter table agent_runs add constraint fk_agent_runs_workspace_id_atom_id_atoms "
                "foreign key (workspace_id, atom_id) references atoms(workspace_id, id)"
            )
        with self.assertRaisesRegex(RuntimeError, "fk_agent_runs_workspace_id_atom_id_atoms"):
            upgrade_atoms(self.url, app_role=self.role)
        with self.engine.connect() as conn:
            self.assert_legacy_preserved(conn)

    def test_timeouts_are_transaction_local_and_parallel_upgrade_is_rejected(self):
        def inspect_timeouts(conn, role):
            self.assertEqual(conn.exec_driver_sql("show lock_timeout").scalar_one(), "10s")
            self.assertEqual(conn.exec_driver_sql("show statement_timeout").scalar_one(), "5min")
            _grant_existing_app_role(conn, role)

        with self.engine.begin() as conn:
            conn.exec_driver_sql("select pg_advisory_xact_lock(6813, 399)")
            with self.assertRaisesRegex(RuntimeError, "another Atoms upgrade"):
                upgrade_atoms(self.url, app_role=self.role)
        with patch("app.db.upgrade_atoms._grant_existing_app_role", side_effect=inspect_timeouts):
            upgrade_atoms(self.url, app_role=self.role)

    def test_missing_or_bypass_runtime_role_is_rejected_without_changes(self):
        with self.engine.connect() as conn:
            owner = conn.exec_driver_sql("select current_user").scalar_one()
        for role in ("missing_runtime_role", owner):
            with self.subTest(role=role), self.assertRaisesRegex(RuntimeError, "app role"):
                upgrade_atoms(self.url, app_role=role)
        with self.engine.connect() as conn:
            self.assert_legacy_preserved(conn)
            self.assertNotIn("atoms", inspect(conn).get_table_names())

    def test_non_bypass_installer_is_rejected_without_role_changes(self):
        from sqlalchemy import event

        engine = create_engine(self.url)

        @event.listens_for(engine, "connect")
        def set_role(dbapi_conn, _):
            dbapi_conn.execute(sql.SQL("set role {}").format(sql.Identifier(self.role)))
            dbapi_conn.commit()

        with patch("app.db.upgrade_atoms.create_engine", return_value=engine):
            with self.assertRaisesRegex(RuntimeError, "upgrade/helper owner"):
                upgrade_atoms(self.url, app_role=self.role)
        with self.engine.connect() as conn:
            self.assert_legacy_preserved(conn)
            self.assertNotIn("atoms", inspect(conn).get_table_names())

    def test_unsafe_existing_helper_owner_is_rejected(self):
        upgrade_atoms(self.url, app_role=self.role)
        with self.engine.begin() as conn:
            conn.connection.driver_connection.execute(
                sql.SQL("alter function public.atom_context_valid() owner to {}").format(
                    sql.Identifier(self.role)
                )
            )
        with self.assertRaisesRegex(RuntimeError, "helper owners cannot bypass"):
            upgrade_atoms(self.url, app_role=self.role)
        with self.engine.connect() as conn:
            self.assert_legacy_preserved(conn)


if __name__ == "__main__":
    unittest.main()
