"""Run-scoped atom access, proven with a real non-superuser PostgreSQL role."""

import unittest
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import db_support
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError
from test_atoms_schema import make_atom, make_connection, make_skill, make_version
from test_schema import T, add, make_workspace

from app.atoms.access import (
    AtomAccessError,
    AtomContext,
    aggregate_session,
    bind_context,
    current_context,
    privileged_atom_session,
    validate_context,
)
from app.db.install import SQL_DIR, grant_app_role
from app.db.session import workspace_session


def setUpModule():
    global ENGINE
    ENGINE = db_support.engine()


class AtomAccessTest(unittest.TestCase):
    def setUp(self):
        self.conn = ENGINE.connect()
        self.tx = self.conn.begin()
        self.ws = make_workspace(self.conn, "access-a")
        self.other_ws = make_workspace(self.conn, "access-b")
        self.atom = make_atom(self.conn, self.ws)
        version = make_version(self.conn, self.atom)
        self.conn.execute(
            update(T("atoms"))
            .where(T("atoms").c.id == self.atom["id"])
            .values(active_version_id=version, status="active")
        )
        self.run = add(
            self.conn,
            "agent_runs",
            workspace_id=self.ws["workspace_id"],
            atom_id=self.atom["id"],
            agent_id=self.atom["member_id"],
            trigger="schedule",
            status="running",
        )
        self.context = AtomContext(
            *(
                str(v)
                for v in (
                    self.ws["workspace_id"],
                    self.atom["id"],
                    self.run,
                    self.atom["member_id"],
                )
            )
        )
        self.project_b = add(
            self.conn,
            "projects",
            workspace_id=self.ws["workspace_id"],
            team_id=self.ws["team"],
            key="OTHER",
            name="Other",
        )
        self.a = self.memory(self.ws["project"], "allowed")
        self.b = self.memory(self.project_b, "denied")
        self.foreign = add(
            self.conn,
            "memories",
            workspace_id=self.other_ws["workspace_id"],
            kind="fact",
            text="foreign",
        )
        grant_app_role(self.conn, "flwn_atom_access_test")

    def tearDown(self):
        if self.tx.is_active:
            self.tx.rollback()
        self.conn.close()

    def memory(self, project, body, **values):
        return add(
            self.conn,
            "memories",
            workspace_id=self.ws["workspace_id"],
            kind="fact",
            text=body,
            project_id=project,
            **values,
        )

    def grant(self, resource="project", resource_id=None, level="read", constraints=None):
        return add(
            self.conn,
            "atom_grants",
            workspace_id=self.ws["workspace_id"],
            atom_id=self.atom["id"],
            resource_type=resource,
            resource_id=resource_id,
            level=level,
            constraints=constraints or {},
        )

    def restrict(self, **overrides):
        self.conn.execute(text("set local role flwn_atom_access_test"))
        values = {
            "workspace_id": self.context.workspace_id,
            "atom_id": self.context.atom_id,
            "run_id": self.context.run_id,
            "member_id": self.context.member_id,
            "service": "off",
            "atom_aggregate": "off",
        }
        values.update(overrides)
        for key, value in values.items():
            self.conn.execute(
                text("select set_config(:key, :value, true)"),
                {"key": f"app.{key}", "value": str(value)},
            )
        self.assertEqual(
            tuple(
                self.conn.execute(
                    text("select rolsuper,rolbypassrls from pg_roles where rolname=current_user")
                ).one()
            ),
            (False, False),
        )

    def admin(self):
        self.conn.execute(text("reset role"))

    def ids(self, table):
        return set(self.conn.scalars(select(T(table).c.id)))

    def rejects(self, action):
        with self.assertRaises(DBAPIError), self.conn.begin_nested():
            action()

    def test_no_grant_denies_all_raw_tables_and_writes(self):
        self.restrict()
        for table in (
            "memories",
            "projects",
            "teams",
            "users",
            "workspaces",
            "files",
            "docs",
            "messages",
            "meetings",
            "chunks",
            "events",
            "llm_usage",
        ):
            with self.subTest(table=table):
                self.assertEqual(self.ids(table), set())
        self.rejects(
            lambda: self.memory(
                self.ws["project"],
                "ungranted",
                created_by=self.atom["member_id"],
                scope="agent",
                owner_member_id=self.atom["member_id"],
            )
        )

    def test_project_grant_is_live_and_tenant_restriction_cannot_be_ored_away(self):
        grant = self.grant(resource_id=self.ws["project"])
        self.restrict(service="on")
        self.assertEqual(self.ids("memories"), {self.a})
        self.admin()
        self.conn.execute(delete(T("atom_grants")).where(T("atom_grants").c.id == grant))
        self.restrict()
        self.assertEqual(self.ids("memories"), set())

    def test_summary_has_no_raw_rows_and_only_internal_aggregate_sees_count(self):
        self.grant(resource_id=self.ws["project"], level="summary")
        self.restrict()
        self.assertEqual(self.ids("memories"), set())
        with aggregate_session(self.context, bind=self.conn) as session:
            self.assertEqual(session.scalar(text("select count(*) from memories")), 1)
        self.conn.execute(text("select set_config('app.atom_aggregate','off',true)"))
        self.assertEqual(self.ids("memories"), set())

    def test_read_cannot_update_or_delete_and_write_cannot_impersonate(self):
        grant = self.grant(resource="memory", level="read")
        self.restrict()
        self.assertEqual(self.conn.execute(update(T("memories")).values(text="bad")).rowcount, 0)
        self.assertEqual(self.conn.execute(delete(T("memories"))).rowcount, 0)
        self.admin()
        self.conn.execute(
            update(T("atom_grants")).where(T("atom_grants").c.id == grant).values(level="write")
        )
        self.restrict()
        self.rejects(
            lambda: self.memory(self.ws["project"], "shared", created_by=self.atom["member_id"])
        )
        self.rejects(
            lambda: self.memory(
                self.ws["project"],
                "spoof",
                scope="agent",
                owner_member_id=self.atom["member_id"],
                created_by=self.ws["human"],
            )
        )
        own = self.memory(
            self.ws["project"],
            "own",
            scope="agent",
            owner_member_id=self.atom["member_id"],
            created_by=self.atom["member_id"],
        )
        self.assertIn(own, self.ids("memories"))
        self.assertEqual(
            self.conn.execute(delete(T("memories")).where(T("memories").c.id == own)).rowcount, 1
        )

    def test_control_plane_and_catalog_mutations_denied_for_every_command(self):
        self.grant(resource="memory", level="write")
        connection = make_connection(self.conn, self.atom, self.ws["human"])
        catalog = make_skill(self.conn)
        self.restrict()
        for table, values in (
            ("atoms", {"max_runs_per_day": 999}),
            ("atom_grants", {"level": "write"}),
            ("atom_connections", {"permission_ceiling": "destructive"}),
            ("catalog_skills", {"instructions": "changed"}),
            ("agent_runs", {"status": "succeeded"}),
        ):
            with self.subTest(table=table):
                self.assertEqual(self.conn.execute(update(T(table)).values(**values)).rowcount, 0)
                self.assertEqual(self.conn.execute(delete(T(table))).rowcount, 0)
        self.rejects(lambda: self.grant(resource="meeting"))
        self.rejects(lambda: make_skill(self.conn, name="forbidden"))
        self.assertIn(connection, self.ids("atom_connections"))
        self.assertIn(catalog, self.ids("catalog_skills"))

    def test_personal_owner_must_be_active_and_private_team_membership_is_live(self):
        self.conn.execute(
            update(T("atoms"))
            .where(T("atoms").c.id == self.atom["id"])
            .values(kind="personal", owner_member_id=self.ws["human"])
        )
        self.conn.execute(
            update(T("teams"))
            .where(T("teams").c.id == self.ws["team"])
            .values(visibility="private")
        )
        self.grant(resource="memory")
        self.restrict()
        self.assertEqual(self.ids("memories"), set())
        self.admin()
        self.conn.execute(
            T("team_members")
            .insert()
            .values(
                workspace_id=self.ws["workspace_id"],
                team_id=self.ws["team"],
                member_id=self.ws["human"],
            )
        )
        self.restrict()
        self.assertEqual(self.ids("memories"), {self.a, self.b})
        self.admin()
        self.conn.execute(
            update(T("members"))
            .where(T("members").c.id == self.ws["human"])
            .values(status="suspended")
        )
        self.restrict()
        self.assertEqual(self.ids("memories"), set())
        with self.assertRaises(AtomAccessError):
            validate_context(self.context, bind=self.conn)

    def test_personal_channel_membership_and_private_memory(self):
        self.conn.execute(
            update(T("atoms"))
            .where(T("atoms").c.id == self.atom["id"])
            .values(kind="personal", owner_member_id=self.ws["human"])
        )
        channel = add(
            self.conn, "channels", workspace_id=self.ws["workspace_id"], type="dm", is_private=True
        )
        message = add(
            self.conn,
            "messages",
            workspace_id=self.ws["workspace_id"],
            channel_id=channel,
            author_id=self.ws["human"],
            body="secret",
        )
        self.grant(resource="channel", resource_id=channel)
        self.grant(resource="memory")
        private = self.memory(
            None, "another agent", scope="agent", owner_member_id=self.ws["agent"]
        )
        self.restrict()
        self.assertNotIn(private, self.ids("memories"))
        self.assertEqual(self.ids("messages"), set())
        self.admin()
        self.conn.execute(
            T("channel_members")
            .insert()
            .values(
                workspace_id=self.ws["workspace_id"], channel_id=channel, member_id=self.ws["human"]
            )
        )
        self.restrict()
        self.assertEqual(self.ids("messages"), {message})

    def test_workspace_atom_cannot_read_other_agents_private_memory(self):
        private = self.memory(
            None, "another agent", scope="agent", owner_member_id=self.ws["agent"]
        )
        self.grant(resource="memory")
        self.restrict()
        self.assertNotIn(private, self.ids("memories"))

    def test_constraints_known_unknown_and_time_window(self):
        old = self.memory(
            self.ws["project"], "old", created_at=datetime.now(UTC) - timedelta(days=60)
        )
        grant = self.grant(resource_id=self.ws["project"], constraints={"since_days": 7})
        self.restrict()
        self.assertEqual(self.ids("memories"), {self.a})
        self.assertNotIn(old, self.ids("memories"))
        for constraints in (
            {"future": True},
            {"since_days": "7"},
            {"since_days": -1},
            {"since_days": 1.5},
            {"labels": "a"},
            {"labels": ["missing"]},
        ):
            self.admin()
            self.conn.execute(
                update(T("atom_grants"))
                .where(T("atom_grants").c.id == grant)
                .values(constraints=constraints)
            )
            self.restrict()
            self.assertEqual(self.ids("memories"), set(), constraints)

    def test_derived_rows_use_canonical_parent_not_forged_scope(self):
        folder = add(self.conn, "folders", workspace_id=self.ws["workspace_id"], name="Folder")
        file_a = add(
            self.conn,
            "files",
            workspace_id=self.ws["workspace_id"],
            folder_id=folder,
            project_id=self.ws["project"],
            kind="document",
            name="A",
            container="files",
            blob_path=f"{self.ws['workspace_id']}/a",
            metadata={"labels": ["customer"]},
        )
        file_b = add(
            self.conn,
            "files",
            workspace_id=self.ws["workspace_id"],
            project_id=self.project_b,
            kind="document",
            name="B",
            container="files",
            blob_path=f"{self.ws['workspace_id']}/b",
        )
        derivative_a = add(
            self.conn,
            "file_derivatives",
            workspace_id=self.ws["workspace_id"],
            file_id=file_a,
            kind="parsed_text",
            content="allowed",
        )
        add(
            self.conn,
            "file_derivatives",
            workspace_id=self.ws["workspace_id"],
            file_id=file_b,
            kind="parsed_text",
            content="denied",
        )
        chunk_a = add(
            self.conn,
            "chunks",
            workspace_id=self.ws["workspace_id"],
            source_type="file",
            source_id=file_a,
            text="allowed",
        )
        add(
            self.conn,
            "chunks",
            workspace_id=self.ws["workspace_id"],
            source_type="file",
            source_id=file_b,
            project_id=self.ws["project"],
            text="denied forged project",
        )
        self.grant(resource="folder", resource_id=folder, constraints={"labels": ["customer"]})
        self.restrict()
        self.assertEqual(self.ids("files"), {file_a})
        self.assertEqual(self.ids("file_derivatives"), {derivative_a})
        self.assertEqual(self.ids("chunks"), {chunk_a})

    def test_every_tenant_table_has_restrictive_read_and_write_policies(self):
        names = self.conn.scalars(
            text(
                "select table_name from information_schema.columns "
                "where table_schema='public' and column_name='workspace_id'"
            )
        ).all()
        for name in [*names, "catalog_skills", "users", "workspaces"]:
            rows = self.conn.execute(
                text(
                    "select cmd,permissive from pg_policies where schemaname='public' "
                    "and tablename=:t and policyname like 'atom_%'"
                ),
                {"t": name},
            ).all()
            self.assertEqual(
                {cmd for cmd, permissive in rows if permissive == "RESTRICTIVE"},
                {"SELECT", "INSERT", "UPDATE", "DELETE"},
                name,
            )

    def test_missing_or_mismatched_run_member_and_atom_are_denied(self):
        self.grant(resource="memory")
        for overrides in (
            {"run_id": ""},
            {"run_id": uuid.uuid4()},
            {"member_id": self.ws["human"]},
            {"atom_id": uuid.uuid4()},
            {"workspace_id": self.other_ws["workspace_id"]},
        ):
            self.restrict(**overrides)
            self.assertEqual(self.ids("memories"), set(), overrides)
        self.restrict()
        self.assertEqual(self.ids("memories"), {self.a, self.b})
        self.admin()
        self.conn.execute(
            update(T("agent_runs"))
            .where(T("agent_runs").c.id == self.run)
            .values(status="succeeded")
        )
        self.restrict()
        self.assertEqual(self.ids("memories"), set())

    def test_finish_retry_context_never_unlocks_terminal_or_paused_raw_rows(self):
        self.grant(resource="memory")
        self.conn.execute(
            update(T("agent_runs"))
            .where(T("agent_runs").c.id == self.run)
            .values(status="succeeded")
        )
        self.restrict()
        with self.assertRaises(AtomAccessError):
            validate_context(self.context, bind=self.conn)
        validate_context(self.context, bind=self.conn, allow_finished=True)
        self.assertEqual(self.ids("memories"), set())
        self.admin()
        self.conn.execute(
            update(T("agent_runs")).where(T("agent_runs").c.id == self.run).values(status="running")
        )
        self.conn.execute(
            update(T("atoms")).where(T("atoms").c.id == self.atom["id"]).values(status="paused")
        )
        self.restrict()
        validate_context(self.context, bind=self.conn)
        self.assertEqual(self.ids("memories"), set())
        self.admin()
        self.conn.execute(
            update(T("atoms")).where(T("atoms").c.id == self.atom["id"]).values(status="killed")
        )
        self.restrict()
        with self.assertRaises(AtomAccessError):
            validate_context(self.context, bind=self.conn, allow_finished=True)

    def test_context_propagates_to_existing_sessions_and_resets(self):
        self.grant(resource_id=self.ws["project"])
        self.restrict()
        with bind_context(self.context), patch("app.db.session.engine", return_value=self.conn):
            from app.db.session import session_for

            with session_for(self.context.workspace_id) as session:
                self.assertEqual(set(session.scalars(select(T("memories").c.id))), {self.a})
            with (
                self.assertRaises(AtomAccessError),
                session_for(str(self.other_ws["workspace_id"])),
            ):
                pass
            with (
                self.assertRaises(AtomAccessError),
                workspace_session(self.conn, self.context.workspace_id, service=True),
            ):
                pass
            with privileged_atom_session(self.context.workspace_id, bind=self.conn) as session:
                self.assertEqual(set(session.scalars(select(T("memories").c.id))), {self.a, self.b})
        self.assertIsNone(current_context())
        with workspace_session(self.conn, self.context.workspace_id) as session:
            self.assertEqual(session.scalar(text("select current_setting('app.atom_id')")), "")
            self.assertEqual(set(session.scalars(select(T("memories").c.id))), {self.a, self.b})

    def test_access_sql_reinstall_is_idempotent(self):
        for _ in range(2):
            self.conn.connection.driver_connection.cursor().execute(
                (SQL_DIR / "40_atom_access.sql").read_text()
            )
        self.restrict()
        self.assertEqual(self.ids("memories"), set())
