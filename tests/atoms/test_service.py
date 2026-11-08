"""Lifecycle/admin and connection-report regressions against disposable Postgres."""

import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import db_support
from factories import T, add, make_workspace
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError

from app.db.atom_access import AtomContext, bind_context
from app.db.install import grant_app_role
from app.db.models.atoms import AtomSchedule, AtomSkill, Skill
from app.db.models.collab import Notification
from app.db.models.memory import AgentRun, Approval, LlmUsage
from app.db.session import workspace_session
from app.domain.atoms.approvals import ApprovalService
from app.domain.atoms.service import AtomService


def setUpModule():
    global ENGINE
    ENGINE = db_support.engine()


class AtomServiceTest(unittest.TestCase):
    def setUp(self):
        with ENGINE.begin() as conn:
            self.ws = make_workspace(conn, f"service-{uuid.uuid4().hex[:10]}")
            conn.execute(
                update(T("members"))
                .where(T("members").c.id == self.ws["human"])
                .values(role="owner")
            )
        self.workspaces = [self.ws["workspace_id"]]
        self.service = AtomService(self.ws["workspace_id"], self.ws["human"], bind=ENGINE)
        self.atom = self.service.create("Reporter", max_runs_per_day=10, max_cost_per_day="10")
        self.aid = self.atom["id"]
        self.version = self.service.propose(self.aid, "Summarize changes")
        self.service.activate(self.aid, self.version["id"])
        self.schedule = self.service.set_schedule(self.aid, interval_minutes=5)
        self.slot = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(hours=2)

    def tearDown(self):
        with ENGINE.begin() as conn:
            members = T("members")
            user_ids = list(
                conn.scalars(
                    select(members.c.user_id).where(
                        members.c.workspace_id.in_(self.workspaces), members.c.user_id.is_not(None)
                    )
                )
            )
            conn.execute(delete(T("workspaces")).where(T("workspaces").c.id.in_(self.workspaces)))
            conn.execute(delete(T("users")).where(T("users").c.id.in_(user_ids)))

    def start(self, step=0):
        return self.service.start_run(
            self.aid, schedule_id=self.schedule["id"], slot=self.slot + timedelta(minutes=5 * step)
        )

    def caller(self, run):
        return AtomService(
            self.ws["workspace_id"],
            self.atom["member_id"],
            atom_id=self.aid,
            run_id=run["id"],
            bind=ENGINE,
        )

    def connection(self):
        return self.service.set_connection(
            self.aid,
            toolkit="outlook",
            composio_user_id="workspace-owner",
            composio_account_ref="account-1",
            toolkit_version="20261001_00",
            permission_ceiling="read",
            status="active",
        )

    def test_stale_release_preserves_cursor_accounting_and_terminal_runs(self):
        terminal = self.start()
        terminal = self.caller(terminal).finish_run(
            terminal["id"],
            status="succeeded",
            cost="0.2",
            tokens_in=3,
            cursor={"page": 2},
            output={"result": "kept"},
        )
        run = self.start(1)
        old = datetime.now(UTC) - timedelta(hours=1)
        with workspace_session(ENGINE, str(self.ws["workspace_id"])) as session:
            row = session.get(AgentRun, uuid.UUID(run["id"]))
            row.started_at = old
            row.cost, row.tokens_in, row.tokens_out = Decimal("0.5"), 8, 4
            row.output = {"partial": "kept", "_actions": 2}
            session.get(AgentRun, uuid.UUID(terminal["id"])).started_at = old
            schedule = session.get(AtomSchedule, uuid.UUID(self.schedule["id"]))
            before = (schedule.cursor, schedule.next_run_at, schedule.last_run_at)
            session.add(
                LlmUsage(
                    workspace_id=self.ws["workspace_id"],
                    run_id=uuid.UUID(run["id"]),
                    member_id=uuid.UUID(self.atom["member_id"]),
                    task_type="atom.call",
                    model="small",
                    input_tokens=8,
                    output_tokens=4,
                    cost_usd=Decimal("0.5"),
                )
            )
        scheduler = AtomService(self.ws["workspace_id"], trusted=True, bind=ENGINE)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda _: scheduler.release_stale(self.aid, older_than_seconds=900), range(2)
                )
            )
        self.assertEqual(sorted(len(result["released_run_ids"]) for result in results), [0, 1])
        self.assertIn({"released_run_ids": [run["id"]]}, results)
        with workspace_session(ENGINE, str(self.ws["workspace_id"])) as session:
            row = session.get(AgentRun, uuid.UUID(run["id"]))
            self.assertEqual(row.status, "failed")
            self.assertIsNotNone(row.ended_at)
            self.assertEqual((row.cost, row.tokens_in, row.tokens_out), (Decimal("0.5"), 8, 4))
            self.assertEqual(row.output, {"partial": "kept", "_actions": 2, "_released": "stale"})
            done = session.get(AgentRun, uuid.UUID(terminal["id"]))
            self.assertEqual((done.status, done.output), ("succeeded", terminal["output"]))
            schedule = session.get(AtomSchedule, uuid.UUID(self.schedule["id"]))
            self.assertEqual((schedule.cursor, schedule.next_run_at, schedule.last_run_at), before)
            usage = list(session.scalars(select(LlmUsage).where(LlmUsage.run_id == row.id)))
            self.assertEqual(len(usage), 1)
            self.assertEqual(usage[0].cost_usd, Decimal("0.5"))
        with self.assertRaises(PermissionError):
            self.caller(run).load()
        self.start(2)
        self.assertEqual(
            scheduler.release_stale(self.aid, older_than_seconds=900), {"released_run_ids": []}
        )

    def test_stale_release_requires_service_and_valid_age(self):
        run = self.start()
        for service in (self.service, self.caller(run)):
            with self.assertRaises(PermissionError):
                service.release_stale(self.aid, older_than_seconds=900)
        scheduler = AtomService(self.ws["workspace_id"], trusted=True, bind=ENGINE)
        for age in (899, -1, True, 900.5, 31536001):
            with self.subTest(age=age), self.assertRaises(ValueError):
                scheduler.release_stale(self.aid, older_than_seconds=age)
        with self.assertRaises(LookupError):
            scheduler.release_stale(uuid.uuid4(), older_than_seconds=900)

    def test_admin_lifecycle_and_immutable_candidate(self):
        other = self.service.propose(self.aid, "New instructions")
        self.assertEqual(other["version"], 2)
        self.assertEqual(self.service.load(self.aid)["version"]["id"], self.version["id"])
        self.service.activate(self.aid, other["id"])
        self.service.rollback(self.aid, self.version["id"])
        self.assertEqual(
            self.service.load(self.aid)["version"]["instructions"], "Summarize changes"
        )
        self.service.configure(self.aid, name="Changed", max_cost_per_day="2.5")
        self.assertEqual(self.service.get(self.aid)["name"], "Changed")
        with self.assertRaises(ValueError):
            self.service.configure(self.aid, active_version_id=other["id"])
        self.service.pause(self.aid)
        with self.assertRaises(ValueError):
            self.start()
        self.service.activate(self.aid, self.version["id"])
        run = self.start()
        self.service.kill(self.aid)
        self.assertFalse(self.service.list_schedules(self.aid)[0]["enabled"])
        with workspace_session(ENGINE, str(self.ws["workspace_id"])) as session:
            self.assertEqual(session.get(AgentRun, uuid.UUID(run["id"])).status, "canceled")
        with self.assertRaises(ValueError):
            self.service.activate(self.aid, self.version["id"])
        self.service.delete(self.aid)
        with self.assertRaises(LookupError):
            self.service.get(self.aid)

    def test_only_active_human_admin_may_administer(self):
        for actor in (self.ws["agent"], None, self.atom["member_id"]):
            with self.subTest(actor=actor), self.assertRaises(PermissionError):
                AtomService(self.ws["workspace_id"], actor, bind=ENGINE).create("Denied")
        for values in (
            {"role": "member"},
            {"role": "viewer"},
            {"role": "admin", "status": "suspended"},
        ):
            with ENGINE.begin() as conn:
                conn.execute(
                    update(T("members"))
                    .where(T("members").c.id == self.ws["human"])
                    .values(**values)
                )
            with self.assertRaises(PermissionError):
                self.service.configure(self.aid, name="Denied")
        with self.assertRaises(PermissionError):
            AtomService(self.ws["workspace_id"], trusted=True, bind=ENGINE).create("Denied")

    def member(self, role="member"):
        with ENGINE.begin() as conn:
            user = add(conn, "users", email=f"m{uuid.uuid4().hex[:8]}@example.com", name="M")
            member = add(
                conn,
                "members",
                workspace_id=self.ws["workspace_id"],
                user_id=user,
                type="HUMAN",
                role=role,
            )
        return member, AtomService(self.ws["workspace_id"], member, bind=ENGINE)

    def test_member_manages_only_own_personal_atom_without_budgets(self):
        me, mine = self.member()
        other, theirs = self.member()
        atom = mine.create("Mine", kind="personal", owner_member_id=me, max_cost_per_day="99")
        self.assertEqual(
            Decimal(atom["max_cost_per_day"]), Decimal("1")
        )  # default caps, not the request
        aid = atom["id"]
        version = mine.propose(aid, "Do it")
        mine.activate(aid, version["id"])
        mine.set_schedule(aid, interval_minutes=5)
        mine.set_connection(
            aid,
            toolkit="outlook",
            composio_user_id=str(me),
            toolkit_version="2026",
            permission_ceiling="read",
        )
        mine.configure(aid, name="Renamed")
        self.assertEqual([a["id"] for a in mine.list()], [aid])
        with self.assertRaises(PermissionError):
            mine.configure(aid, max_cost_per_day="50")
        for denied in (
            lambda: mine.create("Shared"),
            lambda: mine.create("ForOther", kind="personal", owner_member_id=other),
            lambda: mine.configure(self.aid, name="x"),
            lambda: mine.get(self.aid),
            lambda: theirs.get(aid),
            lambda: theirs.configure(aid, name="x"),
            lambda: theirs.delete(aid),
        ):
            with self.assertRaises(PermissionError):
                denied()
        _, viewer = self.member("viewer")
        with self.assertRaises(PermissionError):
            viewer.create("V", kind="personal", owner_member_id=viewer.actor)
        self.service.configure(aid, max_cost_per_day="5")  # an admin still raises the cap
        mine.delete(aid)

    def test_grant_schedule_connection_crud_and_validation(self):
        grant = self.service.set_grant(
            self.aid, resource_type="project", resource_id=self.ws["project"], level="summary"
        )
        same = self.service.set_grant(
            self.aid, resource_type="project", resource_id=self.ws["project"], level="read"
        )
        self.assertEqual(same["id"], grant["id"])
        self.service.revoke_grant(self.aid, grant["id"])
        self.assertEqual(self.service.list_grants(self.aid), [])
        self.service.set_schedule(
            self.aid, schedule_id=self.schedule["id"], cron="0 9 * * *", timezone="Europe/London"
        )
        self.assertEqual(self.service.list_schedules(self.aid)[0]["cron"], "0 9 * * *")
        for kwargs in (
            {"interval_minutes": 1},
            {"interval_minutes": 5, "cron": "* * * * *"},
            {"cron": "bad"},
            {"interval_minutes": 5, "timezone": "bad/zone"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.service.set_schedule(self.aid, **kwargs)
        connection = self.connection()
        with self.assertRaises(ValueError):
            self.service.set_connection(
                self.aid,
                toolkit="outlook",
                composio_user_id="x",
                toolkit_version="latest",
                permission_ceiling="read",
            )
        self.service.delete_connection(self.aid, connection["id"])
        self.service.delete_schedule(self.aid, self.schedule["id"])
        self.assertEqual(self.service.list_connections(self.aid), [])
        self.assertEqual(self.service.list_schedules(self.aid), [])

    def test_run_start_canonical_utc_idempotency_and_concurrency(self):
        local_slot = self.slot.astimezone(timezone(timedelta(hours=3)))
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    self.service.start_run, self.aid, schedule_id=self.schedule["id"], slot=slot
                )
                for slot in (self.slot, local_slot)
            ]
            runs = [f.result() for f in futures]
        self.assertEqual(runs[0]["id"], runs[1]["id"])
        with self.assertRaises(ValueError):
            self.start(1)
        self.assertEqual(
            self.caller(runs[0]).start_run(
                self.aid, schedule_id=self.schedule["id"], slot=self.slot
            )["id"],
            runs[0]["id"],
        )
        with self.assertRaises(PermissionError):
            self.caller(runs[0]).start_run(
                self.aid, schedule_id=self.schedule["id"], slot=self.slot + timedelta(minutes=5)
            )

    def test_trusted_scheduler_does_not_require_human_actor(self):
        scheduler = AtomService(self.ws["workspace_id"], trusted=True, bind=ENGINE)
        run = scheduler.start_run(self.aid, schedule_id=self.schedule["id"], slot=self.slot)
        self.assertEqual(run["status"], "running")
        with self.assertRaises(PermissionError):
            scheduler.pause(self.aid)

    def test_finish_retries_usage_cursor_and_pinned_version(self):
        run = self.start()
        caller = self.caller(run)
        second = self.service.propose(self.aid, "Replacement")
        self.service.activate(self.aid, second["id"])
        self.assertEqual(caller.load()["version"]["id"], self.version["id"])
        proposal = caller.propose_candidate(instructions="Suggestion", source="human")
        self.assertEqual(proposal["source"], "self")
        with self.assertRaises(PermissionError):
            caller.activate(self.aid, proposal["id"])
        first = caller.finish_run(
            run["id"],
            status="succeeded",
            tokens_in=12,
            tokens_out=4,
            cost="1.5",
            cursor={"after": "first"},
        )
        retry = caller.finish_run(
            run["id"], status="failed", tokens_in=99, cost="9", cursor={"after": "wrong"}
        )
        self.assertEqual(first, retry)
        with workspace_session(ENGINE, str(self.ws["workspace_id"])) as session:
            self.assertEqual(
                session.scalar(
                    select(func.count())
                    .select_from(LlmUsage)
                    .where(LlmUsage.run_id == uuid.UUID(run["id"]))
                ),
                1,
            )
            self.assertEqual(
                session.get(AtomSchedule, uuid.UUID(self.schedule["id"])).cursor, {"after": "first"}
            )

    def test_preexisting_usage_is_not_counted_twice(self):
        run = self.start()
        with workspace_session(ENGINE, str(self.ws["workspace_id"])) as session:
            session.add(
                LlmUsage(
                    workspace_id=self.ws["workspace_id"],
                    run_id=uuid.UUID(run["id"]),
                    member_id=uuid.UUID(self.atom["member_id"]),
                    task_type="atom.call",
                    model="small",
                    input_tokens=8,
                    output_tokens=4,
                    cost_usd=Decimal("1"),
                )
            )
        self.caller(run).finish_run(
            run["id"], status="succeeded", tokens_in=10, tokens_out=4, cost="1.5"
        )
        with workspace_session(ENGINE, str(self.ws["workspace_id"])) as session:
            total = session.execute(
                select(func.sum(LlmUsage.input_tokens), func.sum(LlmUsage.cost_usd)).where(
                    LlmUsage.run_id == uuid.UUID(run["id"])
                )
            ).one()
            self.assertEqual(total, (10, Decimal("1.5")))
        self.service.configure(self.aid, max_cost_per_day="2")
        self.assertEqual(self.start(1)["status"], "running")

    def test_daily_caps_include_actions_and_do_not_double_count_usage(self):
        run = self.start()
        self.caller(run).finish_run(run["id"], status="failed", cost="1", actions=3)
        for cap in (
            {"max_runs_per_day": 1},
            {"max_cost_per_day": "1"},
            {"max_actions_per_day": 3},
        ):
            self.service.configure(
                self.aid, max_runs_per_day=10, max_cost_per_day="10", max_actions_per_day=100
            )
            self.service.configure(self.aid, **cap)
            with self.subTest(cap=cap), self.assertRaises(ValueError):
                self.start(1)
        self.service.configure(self.aid, max_cost_per_day="2", max_actions_per_day=100)
        with self.assertRaises(ValueError):
            self.service.start_run(
                self.aid,
                schedule_id=self.schedule["id"],
                slot=self.slot + timedelta(minutes=5),
                estimated_cost="1.01",
            )
        self.assertEqual(self.start(1)["status"], "running")

    def test_connection_report_distinct_runs_pause_and_preserve_metadata(self):
        connection = self.connection()
        for step in range(3):
            run = self.start(step)
            caller = self.caller(run)
            for _ in range(3):
                result = caller.connection_report(
                    run["id"], connection["id"], "expired", "Reconnect"
                )
                self.assertEqual(result["status"], "expired")
            self.assertEqual(
                self.service.get(self.aid)["status"], "paused" if step == 2 else "active"
            )
            with self.assertRaises(ValueError):
                caller.finish_run(run["id"], status="failed", output={"_connection_reports": {}})
            finished = caller.finish_run(
                run["id"], status="failed", output={"message": "connection failed"}
            )
            self.assertIn(connection["id"], finished["output"]["_connection_reports"])

    def test_report_never_edits_connection_configuration_or_other_atom(self):
        connection = self.connection()
        run = self.start()
        caller = self.caller(run)
        for status in ("active", "disabled"):
            with self.assertRaises(ValueError):
                caller.connection_report(run["id"], connection["id"], status)
        with self.assertRaises(PermissionError):
            self.service.report_connection(
                connection["id"], status="revoked", atom_id=self.aid, run_id=run["id"]
            )
        other = self.service.create("Other")
        foreign = self.service.set_connection(
            other["id"],
            toolkit="outlook",
            composio_user_id="other",
            toolkit_version="2026",
            permission_ceiling="read",
        )
        with self.assertRaises(LookupError):
            caller.connection_report(run["id"], foreign["id"], "revoked")
        self.service.pause(self.aid)
        caller.connection_report(run["id"], connection["id"], "unknown")
        current = self.service.list_connections(self.aid)[0]
        self.assertEqual(current["composio_user_id"], connection["composio_user_id"])
        self.assertEqual(current["toolkit_version"], connection["toolkit_version"])
        with self.assertRaises(PermissionError):
            caller.configure(self.aid, name="Unapproved")

    def test_bound_context_cannot_impersonate_human_or_other_run(self):
        run = self.start()
        ctx = AtomContext(str(self.ws["workspace_id"]), self.aid, run["id"], self.atom["member_id"])
        with bind_context(ctx):
            with self.assertRaises(PermissionError):
                self.service.configure(self.aid, name="Impersonated")
            with self.assertRaises(PermissionError):
                self.caller(run).finish_run(str(uuid.uuid4()), status="succeeded")

    def test_out_of_order_cursor_does_not_overwrite_newer_slot(self):
        run = self.start()
        later = self.slot + timedelta(hours=1)
        with workspace_session(ENGINE, str(self.ws["workspace_id"])) as session:
            schedule = session.get(AtomSchedule, uuid.UUID(self.schedule["id"]))
            schedule.last_run_at, schedule.cursor = later, {"after": "newer"}
        self.caller(run).finish_run(run["id"], status="succeeded", cursor={"after": "older"})
        self.assertEqual(self.service.list_schedules(self.aid)[0]["cursor"], {"after": "newer"})

    def test_non_superuser_bound_runtime_and_terminal_retry(self):
        connection = self.connection()
        run = self.start()
        context = AtomContext(
            str(self.ws["workspace_id"]), self.aid, run["id"], self.atom["member_id"]
        )
        with ENGINE.connect() as conn, conn.begin():
            grant_app_role(conn, "flwn_atom_service_test")
            conn.execute(text("set local role flwn_atom_service_test"))
            flags = conn.execute(
                text("select rolsuper, rolbypassrls from pg_roles where rolname=current_user")
            ).one()
            self.assertEqual(tuple(flags), (False, False))
            caller = AtomService(
                self.ws["workspace_id"],
                self.atom["member_id"],
                atom_id=self.aid,
                run_id=run["id"],
                bind=conn,
            )
            with bind_context(context):
                self.assertEqual(caller.load()["id"], self.aid)
                caller.connection_report(run["id"], connection["id"], "expired")
                finished = caller.finish_run(run["id"], status="failed", cost="0.2")
                self.assertEqual(caller.finish_run(run["id"], status="failed", cost="1"), finished)
                with self.assertRaises(PermissionError):
                    caller.load()

    def test_admin_reconnect_resets_distinct_failure_threshold(self):
        connection = self.connection()
        for step in range(2):
            run = self.start(step)
            self.caller(run).connection_report(run["id"], connection["id"], "expired")
            self.caller(run).finish_run(run["id"], status="failed")
        self.connection()
        run = self.start(2)
        self.caller(run).connection_report(run["id"], connection["id"], "expired")
        self.assertEqual(self.service.get(self.aid)["status"], "active")

    def test_tenant_and_personal_owner_boundaries(self):
        with ENGINE.begin() as conn:
            foreign = make_workspace(conn, f"foreign-{uuid.uuid4().hex[:10]}")
            conn.execute(
                update(T("members"))
                .where(T("members").c.id == foreign["human"])
                .values(role="admin")
            )
        self.workspaces.append(foreign["workspace_id"])
        foreign_service = AtomService(foreign["workspace_id"], foreign["human"], bind=ENGINE)
        with self.assertRaises(LookupError):
            foreign_service.get(self.aid)
        with self.assertRaises(LookupError):
            self.service.set_grant(
                self.aid, resource_type="project", resource_id=foreign["project"]
            )
        personal = self.service.create(
            "Personal", kind="personal", owner_member_id=self.ws["human"]
        )
        with self.assertRaises(ValueError):
            self.service.set_connection(
                personal["id"],
                toolkit="outlook",
                composio_user_id="not-owner",
                toolkit_version="2026",
                permission_ceiling="read",
            )
        result = self.service.set_connection(
            personal["id"],
            toolkit="outlook",
            composio_user_id=str(self.ws["human"]),
            toolkit_version="2026",
            permission_ceiling="read",
        )
        self.assertEqual(result["composio_user_id"], str(self.ws["human"]))

    def test_load_complete_live_payload_and_enabled_pinned_skills(self):
        connection = self.connection()
        grant = self.service.set_grant(
            self.aid, resource_type="project", resource_id=self.ws["project"], level="read"
        )
        with workspace_session(ENGINE, str(self.ws["workspace_id"])) as session:
            for version in (1, 2):
                skill = Skill(
                    workspace_id=self.ws["workspace_id"],
                    scope="workspace",
                    name="Pinned",
                    version=version,
                    description="Summary",
                    when_to_use="Daily",
                    instructions=f"Version {version}",
                    tools_required=[],
                )
                session.add(skill)
                session.flush()
                if version == 1:
                    pinned_id = str(skill.id)
                session.add(
                    AtomSkill(
                        workspace_id=self.ws["workspace_id"],
                        atom_id=uuid.UUID(self.aid),
                        skill_id=skill.id,
                        skill_version=version,
                        added_by="human",
                        added_by_member_id=self.ws["human"],
                        enabled=version == 1,
                    )
                )
            session.get(AtomSchedule, uuid.UUID(self.schedule["id"])).cursor = {
                "after": "before-run"
            }
        run = self.start()
        caller = self.caller(run)
        replacement = self.service.propose(self.aid, "New active version")
        self.service.activate(self.aid, replacement["id"])
        loaded = caller.load()
        self.assertEqual(loaded["version"]["id"], self.version["id"])
        self.assertEqual(loaded["grants"][0]["id"], grant["id"])
        self.assertEqual(loaded["schedules"][0]["cursor"], {"after": "before-run"})
        health = loaded["connections"][0]
        for field in (
            "composio_user_id",
            "composio_account_ref",
            "toolkit_version",
            "permission_ceiling",
            "allowed_tools",
            "status",
            "status_detail",
            "status_checked_at",
        ):
            self.assertEqual(health[field], connection[field])
        self.assertNotIn("token", health)
        self.assertEqual(len(loaded["skills"]), 1)
        self.assertEqual(loaded["skills"][0]["id"], pinned_id)
        self.assertEqual(loaded["skills"][0]["instructions"], "Version 1")
        self.assertNotIn("embedding", loaded["skills"][0])
        self.assertNotIn("tsv", loaded["skills"][0])
        self.service.revoke_grant(self.aid, grant["id"])
        self.assertEqual(caller.load()["grants"], [])

    def test_admin_draft_state_and_run_start_resume_cursor(self):
        draft = self.service.create("Draft")
        draft_schedule = self.service.set_schedule(draft["id"], interval_minutes=10)
        info = self.service.get(draft["id"])
        self.assertIsNone(info["version"])
        self.assertEqual(info["schedules"][0]["id"], draft_schedule["id"])
        self.assertEqual(info["grants"], [])
        self.assertEqual(info["connections"], [])
        listed = next(row for row in self.service.list() if row["id"] == draft["id"])
        self.assertEqual(listed["schedules"], info["schedules"])
        scheduler = AtomService(self.ws["workspace_id"], trusted=True, bind=ENGINE)
        self.assertEqual(scheduler.get(draft["id"])["schedules"], info["schedules"])
        self.assertEqual(len(scheduler.list()), 2)
        with workspace_session(ENGINE, str(self.ws["workspace_id"])) as session:
            session.get(AtomSchedule, uuid.UUID(self.schedule["id"])).cursor = {"offset": 42}
        run = self.start()
        self.assertEqual(run["input"]["cursor"], {"offset": 42})
        with workspace_session(ENGINE, str(self.ws["workspace_id"])) as session:
            session.get(AtomSchedule, uuid.UUID(self.schedule["id"])).cursor = {"offset": 100}
        self.assertEqual(self.start()["input"]["cursor"], {"offset": 42})


class ApprovalTests(unittest.TestCase):
    setUp = AtomServiceTest.setUp
    tearDown = AtomServiceTest.tearDown
    start = AtomServiceTest.start
    connection = AtomServiceTest.connection

    @property
    def engine(self):
        return ENGINE

    def caller(self, run):
        return ApprovalService(
            self.ws["workspace_id"],
            self.atom["member_id"],
            atom_id=self.aid,
            run_id=run["id"],
            bind=self.engine,
        )

    def trusted(self, actor=None, workspace=None):
        return ApprovalService(
            workspace or self.ws["workspace_id"],
            actor,
            trusted=True,
            bind=self.engine,
        )

    def request(self, *, assigned=True, **kwargs):
        run = self.start()
        connection = self.connection()
        body = dict(
            kind="atom_action",
            title="Send report",
            request_key="send-report",
            payload={
                "tool_slug": "OUTLOOK_SEND",
                "arguments": {"to": ["example"]},
                "connection_id": connection["id"],
            },
            assigned_to=str(self.ws["human"]) if assigned else None,
        )
        body.update(kwargs)
        return run, self.caller(run).create_approval(**body), body

    def approved(self, **kwargs):
        run, approval, body = self.request(**kwargs)
        self.trusted(self.ws["human"]).decide(approval["id"], status="approved")
        self.caller(run).finish_run(run["id"], status="succeeded")
        return run, approval, body

    def test_create_retry_and_notification_are_one_transaction(self):
        run, approval, body = self.request()
        with ThreadPoolExecutor(max_workers=2) as pool:
            copies = list(pool.map(lambda _: self.caller(run).create_approval(**body), range(2)))
        self.assertEqual([row["id"] for row in copies], [approval["id"]] * 2)
        self.assertEqual(approval["requested_by"], self.atom["member_id"])
        with workspace_session(self.engine, str(self.ws["workspace_id"])) as session:
            notices = list(
                session.scalars(
                    select(Notification).where(Notification.entity_id == uuid.UUID(approval["id"]))
                )
            )
            self.assertEqual(len(notices), 1)
            self.assertEqual(notices[0].recipient_id, self.ws["human"])
        with self.assertRaises(ValueError):
            self.caller(run).create_approval(**{**body, "title": "Changed"})
        # A failed notification insert must roll back the approval itself.
        with patch(
            "app.domain.atoms.approvals.Notification",
            side_effect=RuntimeError("notification failed"),
        ):
            with self.assertRaises(RuntimeError):
                self.caller(run).create_approval(**{**body, "request_key": "rollback"})
        with workspace_session(self.engine, str(self.ws["workspace_id"])) as session:
            self.assertIsNone(
                session.scalar(select(Approval.id).where(Approval.request_key == "rollback"))
            )

    def test_bootstrap_claim_and_outcome_idempotency(self):
        original, approval, body = self.approved()
        self.assertEqual([a["id"] for a in self.trusted().ready()], [approval["id"]])
        with ThreadPoolExecutor(max_workers=2) as pool:
            runs = list(pool.map(lambda _: self.trusted().bootstrap(approval["id"]), range(2)))
        self.assertEqual(runs[0]["id"], runs[1]["id"])
        execution = runs[0]
        self.assertNotEqual(original["id"], execution["id"])
        self.assertEqual(execution["input"]["approval_id"], approval["id"])
        self.assertEqual(execution["input"]["approval_payload"], body["payload"])
        self.assertIsNone(execution["schedule_id"])
        self.assertEqual(execution["trigger"], "schedule")
        self.assertEqual(self.trusted().ready(), [])
        with self.assertRaises(PermissionError):
            self.caller(original).claim(approval["id"])
        with self.assertRaises(PermissionError):
            self.caller(original).outcome(approval["id"], status="succeeded")
        with self.assertRaises(ValueError):
            self.start(1)
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(
                pool.map(lambda _: self.caller(execution).claim(approval["id"]), range(2))
            )
        self.assertEqual(sorted(r["execute"] for r in claims), [False, True])
        caller = self.caller(execution)
        result = caller.outcome(approval["id"], status="succeeded", result={"external_id": "one"})
        retry = caller.outcome(approval["id"], status="failed", result={"external_id": "two"})
        self.assertEqual(result, retry)
        caller.finish_run(execution["id"], status="succeeded")
        self.assertFalse(caller.claim(approval["id"])["execute"])
        self.assertEqual(caller.outcome(approval["id"], status="unknown"), result)

    def test_inflight_original_blocks_bootstrap_and_budget_is_shared(self):
        run, approval, _ = self.request()
        self.trusted(self.ws["human"]).decide(approval["id"], status="approved")
        with self.assertRaisesRegex(ValueError, "running invocation"):
            self.trusted().bootstrap(approval["id"])
        self.caller(run).finish_run(run["id"], status="succeeded", cost="10")
        with self.assertRaisesRegex(ValueError, "budget"):
            self.trusted().bootstrap(approval["id"])

    def test_rejection_and_expiry_never_authorize(self):
        run, approval, _ = self.request()
        human = self.trusted(self.ws["human"])
        human.decide(approval["id"], status="rejected")
        self.assertEqual(human.decide(approval["id"], status="rejected")["status"], "rejected")
        with self.assertRaises(ValueError):
            human.decide(approval["id"], status="approved")
        self.caller(run).finish_run(run["id"], status="succeeded")
        with self.assertRaises(ValueError):
            self.trusted().bootstrap(approval["id"])
        self.assertEqual(self.trusted().ready(), [])

    def test_expiry_on_decision_ready_bootstrap_and_claim(self):
        expires = datetime.now(UTC) + timedelta(minutes=10)
        run, approval, _ = self.request(expires_at=expires)
        human = self.trusted(self.ws["human"])
        real_datetime = datetime
        with patch("app.domain.atoms.approvals.datetime") as clock:
            clock.now.return_value = expires + timedelta(seconds=1)
            with self.assertRaisesRegex(ValueError, "expired"):
                human.decide(approval["id"], status="approved")
        human.decide(approval["id"], status="approved")
        self.caller(run).finish_run(run["id"], status="succeeded")
        execution = self.trusted().bootstrap(approval["id"])
        with patch("app.domain.atoms.approvals.datetime") as clock:
            clock.now.return_value = expires + timedelta(seconds=1)
            self.assertEqual(self.trusted().ready(), [])
            with self.assertRaisesRegex(ValueError, "expired"):
                self.trusted().bootstrap(approval["id"])
            with self.assertRaisesRegex(ValueError, "expired"):
                self.caller(execution).claim(approval["id"])
        self.assertIsNotNone(real_datetime.now(UTC))

    def test_failed_or_crashed_execution_never_requeues(self):
        _, approval, _ = self.approved()
        execution = self.trusted().bootstrap(approval["id"])
        caller = self.caller(execution)
        self.assertTrue(caller.claim(approval["id"])["execute"])
        # A crash after claim is permanently ambiguous; even marking failure is not a retry grant.
        caller.finish_run(execution["id"], status="failed", error="worker lost")
        self.assertFalse(caller.claim(approval["id"])["execute"])
        self.assertEqual(self.trusted().bootstrap(approval["id"])["id"], execution["id"])
        self.assertEqual(self.trusted().ready(), [])
        self.assertEqual(
            caller.outcome(approval["id"], status="unknown")["execution_status"], "unknown"
        )

    def test_failed_unclaimed_run_is_not_recycled(self):
        _, approval, _ = self.approved()
        execution = self.trusted().bootstrap(approval["id"])
        caller = self.caller(execution)
        with self.assertRaises(ValueError):
            caller.outcome(approval["id"], status="succeeded")
        caller.finish_run(execution["id"], status="failed")
        with self.assertRaises(ValueError):
            caller.claim(approval["id"])
        self.assertEqual(self.trusted().ready(), [])
        self.assertEqual(self.trusted().bootstrap(approval["id"])["id"], execution["id"])

    def test_authorization_tenant_atom_and_run_binding(self):
        run, approval, body = self.request()
        with self.assertRaises(PermissionError):
            self.caller(run).decide(approval["id"], status="approved")
        with self.assertRaises(PermissionError):
            self.trusted(self.ws["agent"]).decide(approval["id"], status="approved")
        with self.assertRaises(PermissionError):
            self.trusted().decide(approval["id"], status="approved")
        with self.engine.begin() as conn:
            other = make_workspace(conn, f"other-{uuid.uuid4().hex[:10]}")
        self.workspaces.append(other["workspace_id"])
        with self.assertRaises(LookupError):
            self.trusted(workspace=other["workspace_id"]).bootstrap(approval["id"])
        with self.assertRaises(LookupError):
            self.caller(run).create_approval(
                **{**body, "request_key": "other-human", "assigned_to": other["human"]}
            )
        with self.assertRaises(PermissionError):
            self.caller(run).create_approval(
                **{**body, "request_key": "ai", "assigned_to": self.ws["agent"]}
            )
        second = self.service.create("Other atom")
        wrong = ApprovalService(
            self.ws["workspace_id"],
            self.atom["member_id"],
            atom_id=second["id"],
            run_id=run["id"],
            bind=self.engine,
        )
        with self.assertRaises(PermissionError):
            wrong.get_approval(approval["id"])
        self.trusted(self.ws["human"]).decide(approval["id"], status="approved")
        self.caller(run).finish_run(run["id"], status="succeeded")
        unrelated = self.start(1)
        with self.assertRaises(PermissionError):
            self.caller(unrelated).get_approval(approval["id"])
        self.assertEqual(self.caller(run).get_approval(approval["id"])["id"], approval["id"])

    def test_assignee_member_and_unassigned_admin_policy(self):
        _, approval, _ = self.request()
        with self.engine.begin() as conn:
            conn.execute(
                update(T("members"))
                .where(T("members").c.id == self.ws["human"])
                .values(role="member")
            )
        self.assertEqual(
            self.trusted(self.ws["human"]).decide(approval["id"], status="approved")["status"],
            "approved",
        )

    def test_unassigned_decision_requires_admin_or_personal_owner(self):
        _, approval, _ = self.request(assigned=False)
        with self.engine.begin() as conn:
            conn.execute(
                update(T("members"))
                .where(T("members").c.id == self.ws["human"])
                .values(role="member")
            )
        with self.assertRaises(PermissionError):
            self.trusted(self.ws["human"]).decide(approval["id"], status="approved")
        with self.engine.begin() as conn:
            conn.execute(
                update(T("members"))
                .where(T("members").c.id == self.ws["human"])
                .values(role="admin")
            )
        self.assertEqual(
            self.trusted(self.ws["human"]).decide(approval["id"], status="approved")["status"],
            "approved",
        )

    def test_database_rejects_pending_execution_and_existing_run_conversion(self):
        original, approval, _ = self.request()
        contract = {"approval_id": approval["id"], "approval_payload": approval["payload"]}
        with (
            self.assertRaisesRegex(IntegrityError, "fresh approved unexpired"),
            workspace_session(self.engine, str(self.ws["workspace_id"])) as session,
        ):
            session.add(
                AgentRun(
                    workspace_id=self.ws["workspace_id"],
                    agent_id=uuid.UUID(self.atom["member_id"]),
                    atom_id=uuid.UUID(self.aid),
                    trigger="schedule",
                    status="running",
                    idempotency_key="approval:" + approval["id"],
                    input=contract,
                )
            )
            session.flush()
        self.trusted(self.ws["human"]).decide(approval["id"], status="approved")
        with (
            self.assertRaisesRegex(IntegrityError, "existing runs cannot"),
            self.engine.begin() as conn,
        ):
            conn.execute(
                update(AgentRun)
                .where(AgentRun.id == uuid.UUID(original["id"]))
                .values(
                    input=contract,
                    idempotency_key="approval:" + approval["id"],
                    schedule_id=None,
                )
            )

    def test_immutable_approval_and_execution_input_database_guards(self):
        _, approval, _ = self.approved()
        execution = self.trusted().bootstrap(approval["id"])
        for values in (
            {"payload": {"changed": True}},
            {"title": "changed"},
            {"run_id": uuid.UUID(execution["id"])},
            {"execution_run_id": None},
        ):
            with self.assertRaises(IntegrityError), self.engine.begin() as conn:
                conn.execute(
                    update(Approval)
                    .where(Approval.id == uuid.UUID(approval["id"]))
                    .values(**values)
                )
        with self.assertRaises(IntegrityError), self.engine.begin() as conn:
            conn.execute(
                update(AgentRun)
                .where(AgentRun.id == uuid.UUID(execution["id"]))
                .values(input={"changed": True})
            )
        self.caller(execution).claim(approval["id"])
        with self.assertRaises(IntegrityError), self.engine.begin() as conn:
            conn.execute(
                update(Approval)
                .where(Approval.id == uuid.UUID(approval["id"]))
                .values(claimed_at=None)
            )

    def test_rls_visibility_origin_execution_only(self):
        from sqlalchemy import create_engine, event

        from app.db.install import grant_app_role

        original, approval, _ = self.approved()
        execution = self.trusted().bootstrap(approval["id"])
        with self.engine.begin() as conn:
            grant_app_role(conn, "approval_rls_test")
        restricted = create_engine(self.engine.url)
        self.addCleanup(restricted.dispose)

        @event.listens_for(restricted, "connect")
        def role(connection, _):
            connection.execute("set role approval_rls_test")
            connection.commit()

        for run in (original, execution):
            context = AtomContext(
                str(self.ws["workspace_id"]), self.aid, run["id"], self.atom["member_id"]
            )
            with (
                bind_context(context),
                workspace_session(restricted, str(self.ws["workspace_id"])) as session,
            ):
                self.assertEqual(session.scalar(select(Approval.id)), uuid.UUID(approval["id"]))
                self.assertFalse(
                    session.scalar(
                        text("select atom_row_allowed('approvals', '{}'::jsonb, 'write')")
                    )
                )
        self.caller(execution).finish_run(execution["id"], status="failed")
        unrelated = self.start(1)
        context = AtomContext(
            str(self.ws["workspace_id"]), self.aid, unrelated["id"], self.atom["member_id"]
        )
        with (
            bind_context(context),
            workspace_session(restricted, str(self.ws["workspace_id"])) as session,
        ):
            self.assertIsNone(session.scalar(select(Approval.id)))

    def test_legacy_rows_keep_non_atom_behavior(self):
        with workspace_session(self.engine, str(self.ws["workspace_id"])) as session:
            legacy = Approval(
                workspace_id=self.ws["workspace_id"], kind="action", payload={"old": True}
            )
            session.add(legacy)
            session.flush()
            legacy.payload = {"still": "mutable"}
            legacy.status = "approved"
            session.flush()
            legacy_id = legacy.id
        self.assertEqual(self.trusted().ready(), [])
        with self.assertRaises(ValueError):
            self.trusted().bootstrap(legacy_id)
