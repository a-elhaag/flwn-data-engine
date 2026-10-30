"""Data-preserving upgrades of legacy and existing Atoms schemas on disposable PostgreSQL."""

import unittest
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch

import db_support
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from factories import (
    T,
    add,
    make_atom,
    make_connection,
    make_schedule,
    make_skill,
    make_version,
    make_workspace,
)
from psycopg import sql
from sqlalchemy import (
    CheckConstraint,
    MetaData,
    create_engine,
    delete,
    inspect,
    select,
    text,
    update,
)
from sqlalchemy.exc import DBAPIError
from sqlalchemy.schema import conv

from app.db.base import Base
from app.db.install import SQL_DIR, grant_app_role, install
from app.db.upgrade_atoms import _grant_existing_app_role, upgrade_atoms

"""Atom schema invariants on disposable Postgres, including non-superuser tenant RLS."""


def setUpModule():
    global ENGINE
    ENGINE = db_support.engine()


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
    approvals = metadata.tables["approvals"]
    new_approval_columns = {
        "atom_id",
        "title",
        "request_key",
        "execution_run_id",
        "claimed_at",
        "executed_at",
        "execution_status",
        "execution_result",
    }
    for constraint in list(approvals.constraints):
        if set(constraint.columns.keys()) & new_approval_columns or constraint.name in {
            "ck_approvals_atom_payload",
            "ck_approvals_atom_execution",
            "ck_approvals_execution_status",
        }:
            approvals.constraints.remove(constraint)
            for fk in getattr(constraint, "elements", ()):
                approvals.foreign_keys.remove(fk)
    for index in list(approvals.indexes):
        if index.name == "ix_approvals_atom_ready":
            approvals.indexes.remove(index)
    for name in new_approval_columns:
        approvals._columns.remove(approvals.c[name])
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

    def test_existing_atoms_upgrade_adds_approvals_without_changing_runs(self):
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
                idempotency_key="before-approvals:1",
                tokens_in=7,
                tokens_out=3,
                cost="0.03",
                input={"cursor": {"last_id": "keep"}},
                output={"keep": "result"},
            )
            snapshot = conn.exec_driver_sql(
                "select to_jsonb(r) from agent_runs r where id=%s", (run,)
            ).scalar_one()
            # Simulate the already-installed Atoms release in this disposable database.
            added_columns = set(Base.metadata.tables["approvals"].c.keys()) - set(
                self.legacy.tables["approvals"].c.keys()
            )
            for name in sorted(added_columns):
                conn.connection.driver_connection.execute(
                    sql.SQL("alter table public.approvals drop column {} cascade").format(
                        sql.Identifier(name)
                    )
                )

        for _ in range(2):
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
                    conn.execute(self.legacy.tables["approvals"].select()).all(),
                    self.rows["approvals"],
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
