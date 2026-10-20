"""Approval contract regressions on fixture-owned disposable PostgreSQL."""

import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import test_atoms_service as fixtures
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from test_schema import T, make_workspace

from app.atoms.access import AtomContext, bind_context
from app.atoms.approvals import ApprovalService
from app.db.models.collab import Notification
from app.db.models.memory import AgentRun, Approval
from app.db.session import workspace_session


def setUpModule():
    fixtures.setUpModule()


class ApprovalTests(unittest.TestCase):
    setUp = fixtures.AtomServiceTest.setUp
    tearDown = fixtures.AtomServiceTest.tearDown
    start = fixtures.AtomServiceTest.start
    connection = fixtures.AtomServiceTest.connection

    @property
    def engine(self):
        return fixtures.ENGINE

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
            "app.atoms.approvals.Notification", side_effect=RuntimeError("notification failed")
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
        with patch("app.atoms.approvals.datetime") as clock:
            clock.now.return_value = expires + timedelta(seconds=1)
            with self.assertRaisesRegex(ValueError, "expired"):
                human.decide(approval["id"], status="approved")
        human.decide(approval["id"], status="approved")
        self.caller(run).finish_run(run["id"], status="succeeded")
        execution = self.trusted().bootstrap(approval["id"])
        with patch("app.atoms.approvals.datetime") as clock:
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
