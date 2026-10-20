"""Atom schema invariants on disposable Postgres, including non-superuser tenant RLS."""

import unittest
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import db_support
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError
from test_schema import T, add, make_workspace

from app.db.install import SQL_DIR, grant_app_role


def setUpModule():
    global ENGINE
    ENGINE = db_support.engine()


def make_atom(conn, workspace, **values):
    member_id = add(
        conn,
        "members",
        workspace_id=workspace["workspace_id"],
        type="AI",
        agent_kind="atom",
        name="Reporter",
    )
    atom = {
        "workspace_id": workspace["workspace_id"],
        "member_id": member_id,
        "kind": "workspace",
        "name": "Reporter",
        "model_tier": "small",
        "max_runs_per_day": 10,
        "max_cost_per_day": Decimal("2.50"),
        "max_actions_per_day": 50,
    }
    atom.update(values)
    atom["id"] = add(conn, "atoms", **atom)
    return atom


def make_version(conn, atom, **values):
    data = {
        "workspace_id": atom["workspace_id"],
        "atom_id": atom["id"],
        "version": 1,
        "instructions": "Summarize project updates.",
        "source": "human",
        "created_by": atom["member_id"],
    }
    data.update(values)
    return add(conn, "atom_versions", **data)


def make_schedule(conn, atom, **values):
    data = {"workspace_id": atom["workspace_id"], "atom_id": atom["id"], "interval_minutes": 5}
    data.update(values)
    return add(conn, "atom_schedules", **data)


def make_connection(conn, atom, connected_by, **values):
    data = {
        "workspace_id": atom["workspace_id"],
        "atom_id": atom["id"],
        "toolkit": "outlook",
        "composio_user_id": f"workspace:{atom['workspace_id']}",
        "toolkit_version": "20261001_00",
        "permission_ceiling": "read",
        "connected_by": connected_by,
    }
    data.update(values)
    return add(conn, "atom_connections", **data)


def make_skill(conn, workspace=None, **values):
    data = {
        "name": "summarize",
        "version": 1,
        "description": "Summarize updates",
        "when_to_use": "For a daily report",
        "instructions": "Do not include raw data.",
    }
    if workspace is not None:
        data.update(workspace_id=workspace["workspace_id"], scope="workspace")
    data.update(values)
    return add(conn, "skills" if workspace is not None else "catalog_skills", **data)


class AtomSchemaTest(unittest.TestCase):
    def setUp(self):
        self.conn = ENGINE.connect()
        self.tx = self.conn.begin()
        self.ws = make_workspace(self.conn, "atoms-schema")
        self.atom = make_atom(self.conn, self.ws)

    def tearDown(self):
        self.tx.rollback()
        self.conn.close()

    def rejects(self, action):
        with self.assertRaises(DBAPIError):
            with self.conn.begin_nested():
                action()

    def test_atom_defaults_and_soft_delete_retain_history(self):
        version = make_version(self.conn, self.atom)
        run = add(
            self.conn,
            "agent_runs",
            workspace_id=self.ws["workspace_id"],
            agent_id=self.atom["member_id"],
            atom_id=self.atom["id"],
            atom_version_id=version,
            trigger="schedule",
            idempotency_key="event:1",
        )
        row = self.conn.execute(select(T("atoms")).where(T("atoms").c.id == self.atom["id"])).one()
        self.assertEqual(row.status, "draft")
        self.assertIsNone(row.deleted_at)
        self.conn.execute(
            update(T("atoms"))
            .where(T("atoms").c.id == self.atom["id"])
            .values(status="killed", deleted_at=datetime.now(UTC))
        )
        self.assertEqual(
            self.conn.scalar(select(T("agent_runs").c.atom_id).where(T("agent_runs").c.id == run)),
            self.atom["id"],
        )
        self.assertEqual(self.conn.scalar(select(T("atom_versions").c.id)), version)

    def test_atom_identity_requires_matching_ai_kind(self):
        for member in (self.ws["human"], self.ws["agent"]):
            with self.subTest(member=member):
                self.rejects(
                    lambda member=member: self.conn.execute(
                        update(T("atoms")).values(member_id=member)
                    )
                )
        self.rejects(
            lambda: self.conn.execute(
                update(T("members"))
                .where(T("members").c.id == self.atom["member_id"])
                .values(agent_kind="ghost_engineer")
            )
        )
        self.rejects(lambda: make_atom(self.conn, self.ws, member_id=self.atom["member_id"]))

    def test_personal_owner_and_nonnegative_caps(self):
        self.rejects(lambda: make_atom(self.conn, self.ws, kind="personal"))
        self.rejects(lambda: make_atom(self.conn, self.ws, owner_member_id=self.ws["human"]))
        make_atom(self.conn, self.ws, kind="personal", owner_member_id=self.ws["human"])
        for field in ("max_runs_per_day", "max_cost_per_day", "max_actions_per_day"):
            with self.subTest(field=field):
                self.rejects(lambda field=field: make_atom(self.conn, self.ws, **{field: -1}))
        self.rejects(lambda: make_atom(self.conn, self.ws, max_cost_per_day=Decimal("NaN")))

    def test_activation_and_rollback_require_own_version(self):
        atoms = T("atoms")
        where = atoms.c.id == self.atom["id"]
        self.rejects(lambda: self.conn.execute(update(atoms).where(where).values(status="active")))
        other = make_atom(self.conn, self.ws)
        foreign_version = make_version(self.conn, other)
        self.rejects(
            lambda: self.conn.execute(
                update(atoms).where(where).values(active_version_id=foreign_version)
            )
        )
        first = make_version(self.conn, self.atom)
        second = make_version(self.conn, self.atom, version=2)
        for version in (first, second, first):
            self.conn.execute(
                update(atoms).where(where).values(status="active", active_version_id=version)
            )
            self.assertEqual(
                self.conn.scalar(select(atoms.c.active_version_id).where(where)), version
            )
        self.rejects(
            lambda: self.conn.execute(
                update(atoms).where(where).values(deleted_at=datetime.now(UTC))
            )
        )

    def test_versions_are_append_only_except_status_and_eval(self):
        version = make_version(self.conn, self.atom)
        versions = T("atom_versions")
        for values in (
            {"instructions": "Changed"},
            {"policy": {"danger": "auto"}},
            {"tools": ["tool"]},
            {"source": "self"},
            {"version": 2},
            {"created_by": self.ws["human"]},
            {"id": uuid.uuid4()},
            {"created_at": datetime(2000, 1, 1, tzinfo=UTC)},
        ):
            with self.subTest(values=values):
                self.rejects(
                    lambda values=values: self.conn.execute(update(versions).values(**values))
                )
        self.conn.execute(update(versions).values(status="active", eval={"passed": True}))
        row = self.conn.execute(select(versions).where(versions.c.id == version)).one()
        self.assertEqual(row.status, "active")
        self.assertEqual(row.eval, {"passed": True})
        self.rejects(lambda: self.conn.execute(delete(versions)))
        self.rejects(lambda: make_version(self.conn, self.atom))
        self.rejects(lambda: make_version(self.conn, self.atom, version=0))
        self.rejects(lambda: make_version(self.conn, self.atom, version=2, tools={}))

    def test_soft_deleted_skills_keep_attachments(self):
        for catalog in (False, True):
            with self.subTest(catalog=catalog):
                skill = make_skill(self.conn, None if catalog else self.ws)
                field = "catalog_skill_id" if catalog else "skill_id"
                attachment = add(
                    self.conn,
                    "atom_skills",
                    workspace_id=self.ws["workspace_id"],
                    atom_id=self.atom["id"],
                    **{field: skill},
                    skill_version=1,
                    added_by="human",
                    added_by_member_id=self.ws["human"],
                )
                table = T("catalog_skills" if catalog else "skills")
                self.conn.execute(
                    update(table).where(table.c.id == skill).values(deleted_at=datetime.now(UTC))
                )
                self.assertIsNotNone(
                    self.conn.scalar(
                        select(T("atom_skills").c.id).where(T("atom_skills").c.id == attachment)
                    )
                )

    def test_skill_scope_embedding_and_search_columns(self):
        skill = make_skill(self.conn, self.ws)
        table = T("skills")
        row = self.conn.execute(select(table).where(table.c.id == skill)).one()
        self.assertEqual(row.trust_tier, "community")
        self.assertIn("summarize", row.tsv)
        self.assertNotIn("raw", row.tsv)
        self.assertEqual(table.c.embedding.type.dim, 1536)
        self.assertNotIn("workspace_id", T("catalog_skills").c)
        self.rejects(lambda: make_skill(self.conn, self.ws))
        self.rejects(lambda: make_skill(self.conn, self.ws, name="personal", scope="personal"))
        self.rejects(
            lambda: make_skill(self.conn, self.ws, name="owned", owner_member_id=self.ws["human"])
        )
        make_skill(
            self.conn, self.ws, name="mine", scope="personal", owner_member_id=self.ws["human"]
        )

    def test_skill_attachment_requires_one_exact_version(self):
        skill = make_skill(self.conn, self.ws)
        catalog = make_skill(self.conn)
        data = dict(
            workspace_id=self.ws["workspace_id"],
            atom_id=self.atom["id"],
            skill_version=1,
            added_by="self",
            added_by_member_id=self.atom["member_id"],
        )
        self.rejects(lambda: add(self.conn, "atom_skills", **data))
        self.rejects(
            lambda: add(self.conn, "atom_skills", **data, skill_id=skill, catalog_skill_id=catalog)
        )
        for field, value in (("skill_id", skill), ("catalog_skill_id", catalog)):
            with self.subTest(field=field):
                self.rejects(
                    lambda field=field, value=value: add(
                        self.conn, "atom_skills", **{**data, "skill_version": 2}, **{field: value}
                    )
                )
                add(self.conn, "atom_skills", **data, **{field: value})
                self.rejects(
                    lambda field=field, value=value: add(
                        self.conn, "atom_skills", **data, **{field: value}
                    )
                )

    def test_schedules_require_one_cadence_and_valid_timezone(self):
        schedule = make_schedule(self.conn, self.atom)
        row = (
            self.conn.execute(
                select(T("atom_schedules")).where(T("atom_schedules").c.id == schedule)
            )
            .mappings()
            .one()
        )
        self.assertEqual(row["interval_minutes"], 5)
        self.assertEqual(row["timezone"], "UTC")
        self.assertTrue(row["enabled"])
        self.assertEqual(row["cursor"], {})
        self.assertIsNone(row["last_run_at"])
        self.assertIsNone(row["next_run_at"])
        make_schedule(
            self.conn, self.atom, interval_minutes=None, cron="0 9 * * *", timezone="Europe/Berlin"
        )
        for values in (
            {"interval_minutes": None},
            {"cron": "0 9 * * *"},
            {"interval_minutes": 4},
            {"interval_minutes": 0},
            {"interval_minutes": None, "cron": " "},
            {"timezone": "Not/A_Zone"},
            {"cursor": []},
        ):
            with self.subTest(values=values):
                self.rejects(lambda values=values: make_schedule(self.conn, self.atom, **values))
        self.rejects(
            lambda: self.conn.execute(update(T("atom_schedules")).values(timezone="Not/A_Zone"))
        )
        cursor = {"last_mail_id": "mail-123", "timestamp": "2026-10-10T08:00:00Z"}
        self.conn.execute(
            update(T("atom_schedules"))
            .where(T("atom_schedules").c.id == schedule)
            .values(cursor=cursor)
        )
        self.assertEqual(
            self.conn.scalar(
                select(T("atom_schedules").c.cursor).where(T("atom_schedules").c.id == schedule)
            ),
            cursor,
        )

    def test_only_atom_runs_are_limited_to_schedules(self):
        data = dict(
            workspace_id=self.ws["workspace_id"],
            agent_id=self.atom["member_id"],
            atom_id=self.atom["id"],
        )
        for trigger in ("chat", "ticket_event", "user", "meeting", "webhook", "event"):
            with self.subTest(trigger=trigger):
                self.rejects(
                    lambda trigger=trigger: add(self.conn, "agent_runs", **data, trigger=trigger)
                )
        for trigger in ("chat", "ticket_event", "schedule", "user", "meeting", "webhook"):
            add(
                self.conn,
                "agent_runs",
                workspace_id=self.ws["workspace_id"],
                agent_id=self.ws["agent"],
                trigger=trigger,
            )
        schedule = make_schedule(self.conn, self.atom)
        run = add(self.conn, "agent_runs", **data, trigger="schedule", schedule_id=schedule)
        self.rejects(
            lambda: self.conn.execute(
                update(T("agent_runs")).where(T("agent_runs").c.id == run).values(trigger="webhook")
            )
        )
        self.conn.execute(delete(T("atom_schedules")).where(T("atom_schedules").c.id == schedule))
        row = (
            self.conn.execute(select(T("agent_runs")).where(T("agent_runs").c.id == run))
            .mappings()
            .one()
        )
        self.assertIsNone(row["schedule_id"])
        self.assertEqual(row["workspace_id"], self.ws["workspace_id"])
        self.assertEqual(row["atom_id"], self.atom["id"])

    def test_connections_pin_toolkit_and_allow_only_one_account_per_toolkit(self):
        connection = make_connection(self.conn, self.atom, self.ws["human"])
        row = self.conn.execute(select(T("atom_connections"))).mappings().one()
        self.assertEqual(row["status"], "unknown")
        self.assertEqual(row["allowed_tools"], [])
        self.assertIsNone(row["composio_account_ref"])
        self.assertIsNone(row["status_checked_at"])
        self.rejects(
            lambda: make_connection(
                self.conn, self.atom, self.ws["human"], composio_account_ref="ca_other"
            )
        )
        for values in (
            {"toolkit_version": "latest"},
            {"toolkit_version": " LATEST "},
            {"toolkit_version": ""},
            {"allowed_tools": {}},
            {"allowed_tools": [123]},
            {"allowed_tools": [""]},
            {"allowed_tools": [None]},
            {"status": "failed"},
            {"status": "active"},
            {"composio_user_id": ""},
            {"composio_account_ref": " "},
        ):
            with self.subTest(values=values):
                self.rejects(
                    lambda values=values: self.conn.execute(
                        update(T("atom_connections")).values(**values)
                    )
                )
        now = datetime.now(UTC)
        self.conn.execute(
            update(T("atom_connections"))
            .where(T("atom_connections").c.id == connection)
            .values(
                composio_account_ref="ca_connected",
                status="active",
                status_checked_at=now,
                allowed_tools=["OUTLOOK_LIST_MESSAGES"],
            )
        )
        for status in ("expired", "revoked", "unknown"):
            self.conn.execute(
                update(T("atom_connections")).values(
                    status=status, status_detail="Account unavailable"
                )
            )
        personal = make_atom(self.conn, self.ws, kind="personal", owner_member_id=self.ws["human"])
        make_connection(
            self.conn, personal, self.ws["human"], composio_user_id=str(self.ws["human"])
        )

    def test_wildcard_and_resource_grants_are_unique(self):
        data = dict(
            workspace_id=self.ws["workspace_id"],
            atom_id=self.atom["id"],
            resource_type="project",
            level="read",
        )
        for resource in (None, self.ws["project"]):
            add(self.conn, "atom_grants", **data, resource_id=resource)
            self.rejects(
                lambda resource=resource: add(
                    self.conn, "atom_grants", **data, resource_id=resource
                )
            )
        self.rejects(lambda: add(self.conn, "atom_grants", **{**data, "resource_type": "unknown"}))
        self.rejects(
            lambda: add(
                self.conn, "atom_grants", **{**data, "level": "admin"}, resource_id=uuid.uuid4()
            )
        )

    def test_runs_accounting_approval_and_workspace_idempotency(self):
        data = dict(
            workspace_id=self.ws["workspace_id"],
            agent_id=self.atom["member_id"],
            atom_id=self.atom["id"],
            trigger="schedule",
            idempotency_key=f"{self.atom['id']}:2026-10-10T08:00:00Z",
        )
        run = add(
            self.conn, "agent_runs", **data, tokens_in=12, tokens_out=3, cost=Decimal("0.000123")
        )
        self.rejects(lambda: add(self.conn, "agent_runs", **data))
        for field in ("tokens_in", "tokens_out", "cost"):
            self.rejects(
                lambda field=field: self.conn.execute(update(T("agent_runs")).values(**{field: -1}))
            )
        self.assertEqual(self.conn.scalar(select(T("agent_runs").c.cost)), Decimal("0.000123"))
        add(
            self.conn,
            "llm_usage",
            workspace_id=self.ws["workspace_id"],
            run_id=run,
            member_id=self.atom["member_id"],
            task_type="atom.run",
            model="small",
            input_tokens=12,
            output_tokens=3,
            cost_usd=Decimal("0.000123"),
        )
        add(
            self.conn,
            "approvals",
            workspace_id=self.ws["workspace_id"],
            kind="atom_action",
            run_id=run,
            payload={"tool": "mail.send", "arguments": {}},
        )
        other_ws = make_workspace(self.conn, "other-cost")
        other = make_atom(self.conn, other_ws)
        add(
            self.conn,
            "agent_runs",
            **{
                **data,
                "workspace_id": other_ws["workspace_id"],
                "atom_id": other["id"],
                "agent_id": other["member_id"],
            },
        )
        for _ in range(2):
            add(
                self.conn,
                "agent_runs",
                workspace_id=self.ws["workspace_id"],
                agent_id=self.ws["agent"],
                trigger="user",
            )

    def test_runs_cannot_mix_atoms_versions_schedules_or_members(self):
        other = make_atom(self.conn, self.ws)
        version = make_version(self.conn, other)
        schedule = make_schedule(self.conn, other)
        data = dict(
            workspace_id=self.ws["workspace_id"],
            agent_id=self.atom["member_id"],
            atom_id=self.atom["id"],
            trigger="schedule",
        )
        self.rejects(lambda: add(self.conn, "agent_runs", **data, atom_version_id=version))
        self.rejects(lambda: add(self.conn, "agent_runs", **data, schedule_id=schedule))
        self.rejects(
            lambda: add(self.conn, "agent_runs", **{**data, "agent_id": other["member_id"]})
        )

    def test_cross_workspace_references_are_rejected(self):
        other_ws = make_workspace(self.conn, "foreign-atom")
        other = make_atom(self.conn, other_ws)
        foreign_skill = make_skill(self.conn, other_ws)
        self.rejects(lambda: make_atom(self.conn, self.ws, member_id=other["member_id"]))
        self.rejects(
            lambda: make_atom(
                self.conn, self.ws, kind="personal", owner_member_id=other_ws["human"]
            )
        )
        self.rejects(
            lambda: add(
                self.conn,
                "atom_grants",
                workspace_id=self.ws["workspace_id"],
                atom_id=other["id"],
                resource_type="project",
                level="read",
            )
        )
        self.rejects(lambda: make_connection(self.conn, self.atom, other_ws["human"]))
        self.rejects(lambda: make_schedule(self.conn, other, workspace_id=self.ws["workspace_id"]))
        schedule = make_schedule(self.conn, other)
        self.rejects(
            lambda: add(
                self.conn,
                "agent_runs",
                workspace_id=self.ws["workspace_id"],
                agent_id=self.atom["member_id"],
                atom_id=self.atom["id"],
                schedule_id=schedule,
                trigger="schedule",
            )
        )
        self.rejects(
            lambda: add(
                self.conn,
                "atom_skills",
                workspace_id=self.ws["workspace_id"],
                atom_id=self.atom["id"],
                skill_id=foreign_skill,
                skill_version=1,
                added_by="human",
                added_by_member_id=self.ws["human"],
            )
        )

    def test_new_tenant_tables_have_forced_rls(self):
        tables = (
            "atoms",
            "atom_versions",
            "skills",
            "atom_skills",
            "atom_schedules",
            "atom_connections",
            "atom_grants",
        )
        for table in tables:
            row = self.conn.execute(
                text(
                    "select relrowsecurity, relforcerowsecurity from pg_class where oid = to_regclass(:t)"
                ),
                {"t": table},
            ).one()
            self.assertEqual(tuple(row), (True, True), table)
        row = self.conn.execute(
            text("select relrowsecurity from pg_class where oid = 'catalog_skills'::regclass")
        ).one()
        self.assertTrue(row.relrowsecurity)  # public reads, but atom writes are restricted

    def test_non_superuser_tenant_isolation_and_version_guard(self):
        other_ws = make_workspace(self.conn, "rls-atoms")
        other = make_atom(self.conn, other_ws)
        schedule = make_schedule(self.conn, self.atom)
        connection = make_connection(self.conn, self.atom, self.ws["human"])
        make_schedule(self.conn, other)
        make_connection(self.conn, other, other_ws["human"])
        version = make_version(self.conn, self.atom)
        make_skill(self.conn, self.ws)
        make_skill(self.conn, other_ws)
        catalog = make_skill(self.conn)
        grant_app_role(self.conn, "flwn_atoms_schema_test")
        self.conn.execute(text("set local role flwn_atoms_schema_test"))
        flags = self.conn.execute(
            text("select rolsuper, rolbypassrls from pg_roles where rolname = current_user")
        ).one()
        self.assertEqual(tuple(flags), (False, False))
        self.conn.execute(
            text("select set_config('app.workspace_id', :ws, true)"),
            {"ws": str(self.ws["workspace_id"])},
        )
        self.assertEqual(
            self.conn.execute(select(T("atoms").c.id)).scalars().all(), [self.atom["id"]]
        )
        self.assertEqual(len(self.conn.execute(select(T("skills").c.id)).all()), 1)
        self.assertEqual(
            self.conn.execute(select(T("atom_schedules").c.id)).scalars().all(), [schedule]
        )
        self.assertEqual(
            self.conn.execute(select(T("atom_connections").c.id)).scalars().all(), [connection]
        )
        self.rejects(lambda: make_schedule(self.conn, other))
        self.rejects(lambda: make_connection(self.conn, other, other_ws["human"], toolkit="slack"))
        self.assertEqual(self.conn.scalar(select(T("catalog_skills").c.id)), catalog)
        self.rejects(
            lambda: self.conn.execute(
                update(T("atom_versions"))
                .where(T("atom_versions").c.id == version)
                .values(instructions="bypass")
            )
        )
        self.rejects(lambda: self.conn.execute(delete(T("atom_versions"))))
        self.rejects(lambda: make_atom(self.conn, other_ws))
        self.conn.execute(text("select set_config('app.workspace_id', '', true)"))
        self.assertEqual(self.conn.execute(select(T("atoms").c.id)).all(), [])

    def test_atom_and_schedule_identities_cannot_be_reassigned(self):
        member = add(
            self.conn,
            "members",
            workspace_id=self.ws["workspace_id"],
            type="AI",
            agent_kind="atom",
            name="Replacement",
        )
        self.rejects(lambda: self.conn.execute(update(T("atoms")).values(member_id=member)))
        other = make_atom(self.conn, self.ws, name="Other")
        make_schedule(self.conn, self.atom)
        self.rejects(
            lambda: self.conn.execute(update(T("atom_schedules")).values(atom_id=other["id"]))
        )

    def test_workspace_purge_cascades_through_atom_history(self):
        version = make_version(self.conn, self.atom)
        schedule = make_schedule(self.conn, self.atom)
        self.conn.execute(
            update(T("atoms"))
            .where(T("atoms").c.id == self.atom["id"])
            .values(active_version_id=version, status="active")
        )
        add(
            self.conn,
            "agent_runs",
            workspace_id=self.ws["workspace_id"],
            agent_id=self.atom["member_id"],
            atom_id=self.atom["id"],
            atom_version_id=version,
            schedule_id=schedule,
            trigger="schedule",
        )
        self.conn.execute(
            delete(T("workspaces")).where(T("workspaces").c.id == self.ws["workspace_id"])
        )
        for table in ("atoms", "atom_versions", "agent_runs", "atom_schedules"):
            self.assertEqual(self.conn.execute(select(T(table))).all(), [])

    def test_atom_sql_reinstallation_is_idempotent(self):
        make_version(self.conn, self.atom)
        raw = self.conn.connection.driver_connection.cursor()
        for _ in range(2):
            raw.execute((SQL_DIR / "30_atoms.sql").read_text())
        self.rejects(
            lambda: self.conn.execute(update(T("atom_versions")).values(instructions="changed"))
        )
