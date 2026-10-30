"""REST/MCP approval flow using the existing restricted-role HTTP fixture."""

import json
import time
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import jwt
import signing
from factories import JWKS, KEY
from harness import MemoryHarness
from pydantic import ValidationError
from sqlalchemy import create_engine, event

from app.api.schemas.atoms import ConfigureAtom, ConnectionBody, ScheduleBody
from app.core.config import settings
from app.db.install import grant_app_role
from app.domain.atoms.service import AtomService


class AtomApiTests(MemoryHarness):
    def setUp(self):
        super().setUp()
        with self.engine.begin() as conn:
            grant_app_role(conn, "flwn_atom_api_test")
        application_engine = create_engine(self.engine.url)

        @event.listens_for(application_engine, "connect")
        def restricted_connection(connection, _):
            connection.execute("set role flwn_atom_api_test")
            connection.commit()

        self.addCleanup(application_engine.dispose)
        engine_patch = patch("app.db.session.engine", return_value=application_engine)
        engine_patch.start()
        self.addCleanup(engine_patch.stop)
        self.owner = self.member(self.team_a)
        self.addCleanup(self.sql, "delete from workspaces where id=:id", id=self.team_a)
        self.sql("update members set role='owner' where id=:id", id=self.owner)
        self.admin = self.token(member_id=self.owner, scopes=["atoms:admin"])
        self.base = f"/workspaces/{self.team_a}/atoms"

    def token(self, scopes=("memory:read", "files:read"), ttl_seconds=600, **ids):
        token, _ = signing.mint(self.team_a, set(scopes), "test", ttl_seconds, **ids)
        return {"X-Data-API-Key": "", "Authorization": "Bearer " + token}

    def create_atom(self):
        response = self.client.post(
            self.base,
            headers=self.admin,
            json={
                "name": "Monitor",
                "model_tier": "small",
                "max_runs_per_day": 10,
                "max_cost_per_day": "1.000000",
                "max_actions_per_day": 20,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def active_atom(self):
        atom = self.create_atom()
        path = self.base + "/" + atom["id"]
        version = self.client.post(
            path + "/versions", headers=self.admin, json={"instructions": "Check daily totals."}
        )
        self.assertEqual(version.status_code, 201, version.text)
        schedule = self.client.post(
            path + "/schedules", headers=self.admin, json={"interval_minutes": 5}
        )
        self.assertEqual(schedule.status_code, 201, schedule.text)
        activated = self.client.post(
            path + "/activate", headers=self.admin, json={"version_id": version.json()["id"]}
        )
        self.assertEqual(activated.status_code, 200, activated.text)
        return atom, schedule.json()

    def mcp(self, name, arguments, headers):
        response = self.client.post(
            "/mcp",
            headers={
                **headers,
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["result"]
        if not result.get("isError") and "structuredContent" not in result:
            result["structuredContent"] = json.loads(result["content"][0]["text"])
        return result

    def test_admin_requires_scoped_active_human(self):
        self.assertEqual(self.client.get(self.base).status_code, 403)
        read = self.token(member_id=self.owner, scopes=["memory:read"])
        self.assertEqual(self.client.get(self.base, headers=read).status_code, 403)
        foreign = self.client.get(f"/workspaces/{self.team_b}/atoms", headers=self.admin)
        self.assertEqual(foreign.status_code, 403)
        self.sql("update members set status='suspended' where id=:id", id=self.owner)
        self.assertEqual(self.client.get(self.base, headers=self.admin).status_code, 403)

    def test_admin_crud_and_soft_delete(self):
        atom = self.create_atom()
        path = self.base + "/" + atom["id"]
        self.assertEqual(
            self.client.patch(path, headers=self.admin, json={"status": "active"}).status_code, 422
        )
        self.assertEqual(
            self.client.patch(
                path, headers=self.admin, json={"max_cost_per_day": None}
            ).status_code,
            422,
        )
        self.assertEqual(self.client.delete(path, headers=self.admin).status_code, 204)
        self.assertEqual(self.client.get(path, headers=self.admin).status_code, 404)
        row = self.sql("select status, deleted_at from atoms where id=:id", id=atom["id"])[0]
        self.assertIsNotNone(row["deleted_at"])
        self.assertNotEqual(row["status"], "active")

    def test_schedule_and_connection_administration(self):
        atom, schedule = self.active_atom()
        path = self.base + "/" + atom["id"]
        scheduler = self.client.get(self.base + "/scheduler-state")
        self.assertEqual(scheduler.status_code, 200, scheduler.text)
        self.assertEqual(scheduler.json()[0]["id"], atom["id"])
        self.assertEqual(
            self.client.get(self.base + "/scheduler-state", headers=self.admin).status_code, 403
        )
        schedule_path = path + "/schedules/" + schedule["id"]
        replaced = self.client.put(
            schedule_path, headers=self.admin, json={"interval_minutes": 20, "enabled": False}
        )
        self.assertEqual(replaced.status_code, 200, replaced.text)
        self.assertFalse(replaced.json()["enabled"])
        config = {
            "toolkit": "notes",
            "composio_user_id": f"workspace:{self.team_a}",
            "toolkit_version": "2026_03_15",
            "permission_ceiling": "read",
        }
        connection = self.client.put(path + "/connections", headers=self.admin, json=config)
        self.assertEqual(connection.status_code, 200, connection.text)
        connection_path = path + "/connections/" + connection.json()["id"]
        replaced = self.client.put(
            connection_path, headers=self.admin, json={**config, "toolkit_version": "2026_03_16"}
        )
        self.assertEqual(replaced.status_code, 200, replaced.text)
        self.assertEqual(replaced.json()["toolkit_version"], "2026_03_16")
        self.assertEqual(self.client.delete(connection_path, headers=self.admin).status_code, 200)
        self.assertEqual(self.client.delete(schedule_path, headers=self.admin).status_code, 200)

    def test_existing_memory_routes_recheck_atom_grants(self):
        memory_id = self.remember(self.team_a)
        atom, schedule = self.active_atom()
        path = self.base + "/" + atom["id"]
        started = self.client.post(
            path + "/runs",
            json={
                "schedule_id": schedule["id"],
                "scheduled_for": datetime.now(UTC).isoformat(),
                "idempotency_key": "grant-slot",
            },
        )
        self.assertEqual(started.status_code, 200, started.text)
        headers = self.token(
            member_id=atom["member_id"],
            atom_id=atom["id"],
            run_id=started.json()["id"],
            scopes=["memory:read"],
        )
        memory_path = f"/workspaces/{self.team_a}/memories/{memory_id}"
        self.assertEqual(self.client.get(memory_path, headers=headers).status_code, 404)
        grant = self.client.put(
            path + "/grants",
            headers=self.admin,
            json={
                "resource_type": "memory",
                "resource_id": memory_id,
                "level": "read",
            },
        )
        self.assertEqual(grant.status_code, 200, grant.text)
        self.assertEqual(self.client.get(memory_path, headers=headers).status_code, 200)
        deleted = self.client.delete(path + "/grants/" + grant.json()["id"], headers=self.admin)
        self.assertEqual(deleted.status_code, 204, deleted.text)
        self.assertEqual(self.client.get(memory_path, headers=headers).status_code, 404)

    def test_skill_and_aggregate_mcp_contract(self):
        atom, schedule = self.active_atom()
        path = self.base + "/" + atom["id"]
        start = {
            "schedule_id": schedule["id"],
            "scheduled_for": datetime.now(UTC).isoformat(),
            "idempotency_key": "skills-slot",
        }
        started = self.client.post(path + "/runs", json=start)
        self.assertEqual(started.status_code, 200, started.text)
        headers = self.token(
            member_id=atom["member_id"],
            atom_id=atom["id"],
            run_id=started.json()["id"],
            scopes=[
                "atoms:read",
                "atoms:run",
                "atoms:propose",
                "skills:read",
                "skills:write",
                "aggregate:read",
            ],
        )
        restarted = self.mcp("atom_run_start", start, headers)
        self.assertFalse(restarted.get("isError"), restarted)
        self.assertEqual(restarted["structuredContent"]["id"], started.json()["id"])
        proposed = self.mcp(
            "atom_propose_version", {"instructions": "Candidate instructions"}, headers
        )
        self.assertFalse(proposed.get("isError"), proposed)
        self.assertEqual(proposed["structuredContent"]["status"], "candidate")
        skill = self.mcp(
            "skill_write",
            {
                "name": "Daily count",
                "description": "Count daily totals",
                "when_to_use": "For a daily report",
                "instructions": "Read the aggregate count.",
                "scope": "workspace",
            },
            headers,
        )
        self.assertFalse(skill.get("isError"), skill)
        skill_id = skill["structuredContent"]["id"]
        attached = self.mcp("skill_attach", {"skill_id": skill_id, "skill_version": 1}, headers)
        self.assertFalse(attached.get("isError"), attached)
        searched = self.mcp("skills_search", {"query": "daily totals"}, headers)
        self.assertFalse(searched.get("isError"), searched)
        self.assertIn(skill_id, [s["id"] for s in searched["structuredContent"]["skills"]])
        self.assertTrue(
            all("instructions" not in s for s in searched["structuredContent"]["skills"])
        )
        grant = self.client.put(
            path + "/grants",
            headers=self.admin,
            json={"resource_type": "memory", "level": "summary"},
        )
        self.assertEqual(grant.status_code, 200, grant.text)
        aggregate = self.mcp("aggregate_read", {"resource_type": "memory"}, headers)
        self.assertFalse(aggregate.get("isError"), aggregate)
        self.assertEqual(set(aggregate["structuredContent"]), {"count"})
        connection = self.client.put(
            path + "/connections",
            headers=self.admin,
            json={
                "toolkit": "outlook",
                "composio_user_id": "workspace:" + self.team_a,
                "toolkit_version": "20260101",
                "permission_ceiling": "read",
            },
        )
        self.assertEqual(connection.status_code, 200, connection.text)
        report = self.mcp(
            "atom_connection_report",
            {
                "connection_id": connection.json()["id"],
                "status": "revoked",
                "detail": "Account revoked",
            },
            headers,
        )
        self.assertFalse(report.get("isError"), report)
        loaded = self.mcp("atom_load", {}, headers)
        self.assertFalse(loaded.get("isError"), loaded)
        content = loaded["structuredContent"]
        self.assertEqual(content["skills"][0]["instructions"], "Read the aggregate count.")
        self.assertEqual(content["grants"][0]["level"], "summary")
        self.assertEqual(content["schedules"][0]["id"], schedule["id"])
        self.assertEqual(content["connections"][0]["status"], "revoked")
        attachment_id = attached["structuredContent"]["id"]
        disabled = self.client.patch(
            path + "/skills/" + attachment_id, headers=self.admin, json={"enabled": False}
        )
        self.assertEqual(disabled.status_code, 200, disabled.text)
        self.assertEqual(self.mcp("atom_load", {}, headers)["structuredContent"]["skills"], [])
        deleted = self.client.delete(
            f"/workspaces/{self.team_a}/skills/{skill_id}", headers=self.admin
        )
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(
            self.mcp("skills_search", {"query": "Daily count"}, headers)["structuredContent"][
                "skills"
            ],
            [],
        )

    def test_scheduler_domain_error_prefixes(self):
        atom, schedule = self.active_atom()
        path = self.base + "/" + atom["id"]
        body = {
            "schedule_id": schedule["id"],
            "idempotency_key": "errors",
            "scheduled_for": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        }
        not_due = self.client.post(path + "/runs", json=body)
        self.assertEqual(not_due.status_code, 422)
        self.assertTrue(not_due.json()["detail"].startswith("not_due:"))
        self.client.patch(path, headers=self.admin, json={"max_runs_per_day": 0})
        body["scheduled_for"] = datetime.now(UTC).isoformat()
        cap = self.client.post(path + "/runs", json=body)
        self.assertEqual(cap.status_code, 422)
        self.assertTrue(cap.json()["detail"].startswith("cap_exhausted:"))
        self.client.patch(path, headers=self.admin, json={"max_runs_per_day": 10})
        self.assertEqual(self.client.post(path + "/runs", json=body).status_code, 200)
        body["scheduled_for"] = datetime.now(UTC).isoformat()
        busy = self.client.post(path + "/runs", json=body)
        self.assertEqual(busy.status_code, 422)
        self.assertTrue(busy.json()["detail"].startswith("busy:"))

    def test_scheduler_filters_and_stale_release_contract(self):
        atom, schedule = self.active_atom()
        draft = self.create_atom()
        path = self.base + "/" + atom["id"]
        disabled = self.client.post(
            path + "/schedules", headers=self.admin, json={"interval_minutes": 10, "enabled": False}
        )
        self.assertEqual(disabled.status_code, 201, disabled.text)
        state = self.client.get(self.base + "/scheduler-state?status=active&enabled=true")
        self.assertEqual(state.status_code, 200, state.text)
        self.assertEqual([item["id"] for item in state.json()], [atom["id"]])
        self.assertEqual([item["id"] for item in state.json()[0]["schedules"]], [schedule["id"]])
        disabled_state = self.client.get(self.base + "/scheduler-state?enabled=false")
        self.assertEqual(
            [item["id"] for item in disabled_state.json()[0]["schedules"]], [disabled.json()["id"]]
        )
        drafts = self.client.get(self.base + "/scheduler-state?status=draft")
        self.assertEqual([item["id"] for item in drafts.json()], [draft["id"]])
        self.assertEqual(
            self.client.get(self.base + "/scheduler-state?status=bogus").status_code, 422
        )
        started = self.client.post(
            path + "/runs",
            json={
                "schedule_id": schedule["id"],
                "scheduled_for": datetime.now(UTC).isoformat(),
                "idempotency_key": "stale-slot",
            },
        )
        self.assertEqual(started.status_code, 200, started.text)
        worker = self.token(
            member_id=atom["member_id"],
            atom_id=atom["id"],
            run_id=started.json()["id"],
            scopes=["atoms:read", "atoms:run"],
        )
        release = path + "/runs/release-stale"
        for headers in (self.admin, worker):
            self.assertEqual(
                self.client.post(
                    release, headers=headers, json={"older_than_seconds": 900}
                ).status_code,
                403,
            )
        for body in (
            {},
            {"older_than_seconds": 899},
            {"older_than_seconds": 900.5},
            {"older_than_seconds": True},
            {"older_than_seconds": 31536001},
        ):
            self.assertEqual(self.client.post(release, json=body).status_code, 422)
        self.sql(
            "update agent_runs set started_at=now()-interval '1 hour' where id=:id",
            id=started.json()["id"],
        )
        for status in ("queued", "waiting_approval", "succeeded", "failed", "canceled"):
            self.sql(
                "update agent_runs set status=:status where id=:id",
                status=status,
                id=started.json()["id"],
            )
            self.assertEqual(
                self.client.post(release, json={"older_than_seconds": 900}).json(),
                {"released_run_ids": []},
            )
        self.sql("update agent_runs set status='running' where id=:id", id=started.json()["id"])
        for expected in ([started.json()["id"]], []):
            response = self.client.post(release, json={"older_than_seconds": 900})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), {"released_run_ids": expected})
        denied = self.mcp("atom_load", {}, worker)
        self.assertTrue(denied.get("isError"), denied)
        self.assertIn("denied:", denied["content"][0]["text"])

    def test_scheduler_bootstrap_mcp_and_finish_retry(self):
        atom, schedule = self.active_atom()
        path = self.base + "/" + atom["id"]
        start_body = {
            "schedule_id": schedule["id"],
            "scheduled_for": datetime.now(UTC).isoformat(),
            "idempotency_key": "integration-slot",
        }
        denied = self.client.post(path + "/runs", headers=self.admin, json=start_body)
        self.assertEqual(denied.status_code, 403, denied.text)
        started = self.client.post(path + "/runs", json=start_body)
        self.assertEqual(started.status_code, 200, started.text)
        run = started.json()
        repeated = self.client.post(
            path + "/runs", json={**start_body, "idempotency_key": "different-client-key"}
        )
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertEqual(repeated.json()["id"], run["id"])
        headers = self.token(
            member_id=atom["member_id"],
            atom_id=atom["id"],
            run_id=run["id"],
            scopes=["atoms:read", "atoms:run", "atoms:propose"],
        )
        replacement = self.token(
            member_id=atom["member_id"],
            atom_id=atom["id"],
            run_id=run["id"],
            scopes=["atoms:read", "atoms:run"],
            ttl_seconds=500,
        )
        self.assertNotEqual(headers["Authorization"], replacement["Authorization"])
        for valid in (replacement, headers):
            self.assertFalse(self.mcp("atom_load", {}, valid).get("isError"))
        for cost in (0.018, 0, -1, True, "NaN", "Infinity", "-0.01", "0.0000001", "1000000"):
            invalid = self.mcp("atom_run_finish", {"status": "succeeded", "cost": cost}, headers)
            self.assertTrue(invalid.get("isError"), (cost, invalid))
        schema = self.client.post(
            "/mcp",
            headers={**headers, "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ).json()
        finish_schema = next(
            tool for tool in schema["result"]["tools"] if tool["name"] == "atom_run_finish"
        )
        self.assertEqual(finish_schema["inputSchema"]["properties"]["cost"]["type"], "string")
        loaded = self.mcp("atom_load", {}, headers)
        self.assertFalse(loaded.get("isError"), loaded)
        self.assertEqual(
            self.client.patch(path, headers=headers, json={"status": "paused"}).status_code, 403
        )
        report = self.mcp(
            "atom_connection_report", {"connection_id": atom["id"], "status": "active"}, headers
        )
        self.assertTrue(report.get("isError"), report)
        finished = {
            "status": "succeeded",
            "tokens_in": 12,
            "tokens_out": 3,
            "cost": "0.018000",
            "model": "test-small",
            "cursor": {"page": 2},
        }
        for _ in range(2):
            result = self.mcp("atom_run_finish", finished, headers)
            self.assertFalse(result.get("isError"), result)
        row = self.sql("select cost, tokens_in, model from agent_runs where id=:id", id=run["id"])[
            0
        ]
        self.assertEqual(str(row["cost"]), "0.018000")
        self.assertEqual(row["tokens_in"], 12)
        self.assertEqual(row["model"], "test-small")
        usage = self.sql("select model from llm_usage where run_id=:id", id=run["id"])
        self.assertEqual(usage, [{"model": "test-small"}])
        denied_load = self.mcp("atom_load", {}, headers)
        self.assertTrue(denied_load.get("isError"), denied_load)


class ApprovalApiTests(AtomApiTests):
    def approval_fixture(self):
        atom, schedule = self.active_atom()
        path = self.base + "/" + atom["id"]
        connection = self.client.put(
            path + "/connections",
            headers=self.admin,
            json={
                "toolkit": "outlook",
                "composio_user_id": "workspace",
                "composio_account_ref": "account",
                "toolkit_version": "20261001_00",
                "permission_ceiling": "write",
                "status": "active",
                "allowed_tools": ["OUTLOOK_SEND"],
            },
        )
        self.assertEqual(connection.status_code, 200, connection.text)
        run = self.client.post(
            path + "/runs",
            json={
                "schedule_id": schedule["id"],
                "scheduled_for": datetime.now(UTC).isoformat(),
                "idempotency_key": "approval-origin",
            },
        )
        self.assertEqual(run.status_code, 200, run.text)
        run = run.json()
        token = self.token(
            member_id=atom["member_id"], atom_id=atom["id"], run_id=run["id"], scopes=["atoms:run"]
        )
        body = {
            "kind": "atom_action",
            "title": "Send report",
            "request_key": "send-1",
            "payload": {
                "tool_slug": "OUTLOOK_SEND",
                "arguments": {"subject": "Report"},
                "connection_id": connection.json()["id"],
            },
            "assigned_to": self.owner,
        }
        self.approvals = f"/workspaces/{self.team_a}/approvals"
        return atom, run, token, body

    def finish(self, atom, run, status="succeeded"):
        AtomService(
            self.team_a, atom["member_id"], atom_id=atom["id"], run_id=run["id"], bind=self.engine
        ).finish_run(run["id"], status=status)

    def test_approval_mcp_create_rest_human_bootstrap_claim_outcome(self):
        atom, original, token, body = self.approval_fixture()
        body.pop("request_key")
        created = self.mcp("atom_approval_create", body, token)
        self.assertFalse(created.get("isError"), created)
        approval = created["structuredContent"]
        retry = self.client.post(self.approvals, headers=token, json=body)
        self.assertEqual(retry.status_code, 201, retry.text)
        self.assertEqual(retry.json()["id"], approval["id"])
        path = self.approvals + "/" + approval["id"]
        for headers in (token, self.admin, {}):
            response = self.client.put(
                path + "/decision", headers=headers, json={"status": "approved"}
            )
            self.assertEqual(response.status_code, 403, response.text)
        decided = self.client.put(
            path + "/decision",
            headers={"X-Acting-Member-Id": self.owner},
            json={"status": "approved"},
        )
        self.assertEqual(decided.status_code, 200, decided.text)
        self.assertEqual(len(self.client.get(self.approvals + "/ready").json()), 1)
        self.assertEqual(self.client.get(self.approvals + "/ready", headers=token).status_code, 403)
        self.assertEqual(
            self.client.post(path + "/execution-run", headers=token, json={}).status_code, 403
        )
        busy = self.client.post(path + "/execution-run", json={})
        self.assertEqual(busy.status_code, 422, busy.text)
        self.finish(atom, original)
        execution = self.client.post(path + "/execution-run", json={})
        self.assertEqual(execution.status_code, 200, execution.text)
        execution = execution.json()
        headers = self.token(
            member_id=atom["member_id"],
            atom_id=atom["id"],
            run_id=execution["id"],
            scopes=["atoms:run"],
        )
        self.assertEqual(self.client.get(path, headers=headers).status_code, 200)
        self.assertEqual(self.client.post(path + "/claim", headers=token).status_code, 403)
        first = self.mcp("atom_approval_claim", {"approval_id": approval["id"]}, headers)
        self.assertFalse(first.get("isError"), first)
        self.assertTrue(first["structuredContent"]["execute"])
        self.assertFalse(self.client.post(path + "/claim", headers=headers).json()["execute"])
        outcome = self.client.put(
            path + "/outcome",
            headers=headers,
            json={"status": "succeeded", "result": {"id": "external"}},
        )
        self.assertEqual(outcome.status_code, 200, outcome.text)
        self.finish(atom, execution)
        retry = self.client.put(path + "/outcome", headers=headers, json={"status": "unknown"})
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(retry.json(), outcome.json())
        for tool, arguments in (
            ("atom_approval_get", {}),
            ("atom_approval_claim", {}),
            ("atom_approval_outcome", {"status": "failed"}),
        ):
            result = self.mcp(tool, {"approval_id": approval["id"], **arguments}, headers)
            self.assertFalse(result.get("isError"), result)
            stored = result["structuredContent"]
            if tool == "atom_approval_claim":
                self.assertFalse(stored["execute"])
            else:
                self.assertEqual(stored, outcome.json())
        self.assertEqual(self.client.get(self.approvals + "/ready").json(), [])
        notifications = self.sql(
            "select * from notifications where entity_id=:id", id=approval["id"]
        )
        self.assertEqual(len(notifications), 1)

    def test_approval_validation_tenant_and_scope(self):
        atom, run, token, body = self.approval_fixture()
        for extra in ({"requested_by": self.owner}, {"run_id": run["id"]}, {"atom_id": atom["id"]}):
            self.assertEqual(
                self.client.post(self.approvals, headers=token, json={**body, **extra}).status_code,
                422,
            )
        invalid = {**body, "payload": {**body["payload"], "tool_slug": "NOT_ALLOWED"}}
        self.assertEqual(
            self.client.post(self.approvals, headers=token, json=invalid).status_code, 403
        )
        self.assertEqual(self.client.post(self.approvals, json=body).status_code, 403)
        self.assertEqual(
            self.client.post(
                f"/workspaces/{self.team_b}/approvals", headers=token, json=body
            ).status_code,
            403,
        )
        unscoped = self.token(
            member_id=atom["member_id"], atom_id=atom["id"], run_id=run["id"], scopes=["atoms:read"]
        )
        self.assertEqual(
            self.client.post(self.approvals, headers=unscoped, json=body).status_code, 403
        )
        denied = self.mcp("atom_approval_create", body, unscoped)
        self.assertTrue(denied.get("isError"), denied)

    def test_approval_expired_claim_rejected_and_assignee_access(self):
        atom, run, token, body = self.approval_fixture()
        expires = datetime.now(UTC) + timedelta(minutes=10)
        body["expires_at"] = expires.isoformat()
        created = self.client.post(self.approvals, headers=token, json=body)
        self.assertEqual(created.status_code, 201, created.text)
        path = self.approvals + "/" + created.json()["id"]
        another = self.member(self.team_a)
        self.sql("update members set role='owner' where id=:id", id=another)
        self.assertEqual(
            self.client.put(
                path + "/decision",
                headers={"X-Acting-Member-Id": another},
                json={"status": "approved"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.put(
                path + "/decision",
                headers={"X-Acting-Member-Id": self.owner},
                json={"status": "approved"},
            ).status_code,
            200,
        )
        self.finish(atom, run)
        execution = self.client.post(path + "/execution-run", json={}).json()
        token = self.token(
            member_id=atom["member_id"],
            atom_id=atom["id"],
            run_id=execution["id"],
            scopes=["atoms:run"],
        )
        with patch("app.domain.atoms.approvals.datetime") as clock:
            clock.now.return_value = expires + timedelta(seconds=1)
            response = self.client.post(path + "/claim", headers=token)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("expired", response.text)


class SignedAtomAdminTests(MemoryHarness):
    mcp = AtomApiTests.mcp
    token = AtomApiTests.token
    create_atom = AtomApiTests.create_atom
    active_atom = AtomApiTests.active_atom

    def setUp(self):
        super().setUp()
        with self.engine.begin() as conn:
            grant_app_role(conn, "flwn_atom_signed_test")
        application_engine = create_engine(self.engine.url)

        @event.listens_for(application_engine, "connect")
        def restricted_connection(connection, _):
            connection.execute("set role flwn_atom_signed_test")
            connection.commit()

        self.addCleanup(application_engine.dispose)
        engine_patch = patch("app.db.session.engine", return_value=application_engine)
        engine_patch.start()
        self.addCleanup(engine_patch.stop)
        self.owner = self.member(self.team_a)
        self.addCleanup(self.sql, "delete from workspaces where id=:id", id=self.team_a)
        self.sql("update members set role='owner' where id=:id", id=self.owner)
        self.admin = self.token(member_id=self.owner, scopes=["atoms:admin"])
        self.base = f"/workspaces/{self.team_a}/atoms"
        patcher = patch.object(settings, "TOKEN_JWKS", JWKS)
        patcher.start()
        self.addCleanup(patcher.stop)

    def headers(self, member, scope="atoms:admin"):
        now = int(time.time())
        claims = {
            "iss": settings.TOKEN_ISSUER,
            "aud": ["atoms-service", settings.TOKEN_AUDIENCE],  # the Atomizer forwards it on
            "sub": member,
            "ws": self.team_a,
            "typ": "human",
            "ctx": "workspace",
            "mem": member,
            "scope": scope,
            "iat": now,
            "exp": now + 900,
            "jti": uuid.uuid4().hex,
        }
        token = jwt.encode(claims, KEY, algorithm="EdDSA", headers={"kid": "k1"})
        return {"X-Data-API-Key": "", "Authorization": f"Bearer {token}"}

    def test_admin_member_manages_atoms_as_themselves(self):
        headers = self.headers(self.owner)
        listed = self.client.get(self.base, headers=headers)
        self.assertEqual(listed.status_code, 200, listed.text)

    def test_header_cannot_change_who_acts(self):
        other = self.member(self.team_a)
        headers = {**self.headers(self.owner), "X-Acting-Member-Id": other}
        body = {
            "name": "Mine",
            "model_tier": "small",
            "max_runs_per_day": 1,
            "max_cost_per_day": "1.000000",
            "max_actions_per_day": 1,
        }
        created = self.client.post(self.base, headers=headers, json=body)
        self.assertEqual(created.status_code, 201, created.text)

    def test_other_scope_and_scheduler_routes_are_refused(self):
        weak = self.headers(self.owner, scope="atoms:read")
        self.assertEqual(self.client.get(self.base, headers=weak).status_code, 403)
        strong = self.headers(self.owner)
        self.assertEqual(
            self.client.get(self.base + "/scheduler-state", headers=strong).status_code, 403
        )

    def test_session_route_accepts_a_good_token_and_refuses_a_revoked_one(self):
        headers = self.headers(self.owner)
        ok = self.client.get("/auth/session", headers=headers)
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(ok.json()["member_id"], self.owner)
        jwt_token = headers["Authorization"][7:]
        jti = jwt.decode(jwt_token, options={"verify_signature": False})["jti"]
        body = {"jti": jti, "expires_at": int(time.time()) + 900}
        self.assertEqual(self.client.post("/auth/revocations", json=body).status_code, 204)
        self.assertEqual(self.client.get("/auth/session", headers=headers).status_code, 401)
        self.assertEqual(
            self.client.get("/auth/session", headers={"X-Data-API-Key": ""}).status_code, 401
        )

    def test_session_route_does_not_vouch_for_run_tokens(self):

        run_token, _ = signing.mint(
            self.team_a,
            {"atoms:read"},
            "atom-worker",
            300,
            self.owner,
            atom_id=str(uuid.uuid4()),
            run_id=str(uuid.uuid4()),
            ctx="workspace",
        )
        headers = {"X-Data-API-Key": "", "Authorization": f"Bearer {run_token}"}
        self.assertEqual(self.client.get("/auth/session", headers=headers).status_code, 403)

    def test_scheduler_workspaces_lists_only_workspaces_with_work(self):
        self.assertNotIn(
            self.team_a, self.client.get("/atoms/scheduler-workspaces").json()["workspace_ids"]
        )
        self.active_atom()
        found = self.client.get("/atoms/scheduler-workspaces").json()["workspace_ids"]
        self.assertIn(self.team_a, found)
        self.assertNotIn(self.team_b, found)
        denied = self.client.get("/atoms/scheduler-workspaces", headers=self.headers(self.owner))
        self.assertEqual(denied.status_code, 401)

    def test_whole_deploy_flow_runs_on_the_forwarded_token_alone(self):
        h = self.headers(self.owner)  # what the Atomizer sends: no service key, no acting header
        body = {
            "name": "Mail",
            "model_tier": "small",
            "max_runs_per_day": 5,
            "max_cost_per_day": "2.000000",
            "max_actions_per_day": 50,
        }
        atom = self.client.post(self.base, headers=h, json=body)
        self.assertEqual(atom.status_code, 201, atom.text)
        path = f"{self.base}/{atom.json()['id']}"
        steps = [
            ("patch", "PATCH", path, {"description": "Daily mail"}, 200),
            ("version", "POST", path + "/versions", {"instructions": "Read mail."}, 201),
            ("grant", "PUT", path + "/grants", {"resource_type": "memory", "level": "read"}, 200),
            (
                "connection",
                "PUT",
                path + "/connections",
                {
                    "toolkit": "outlook",
                    "composio_user_id": "u1",
                    "toolkit_version": "1.0.0",
                    "permission_ceiling": "read",
                    "allowed_tools": ["OUTLOOK_LIST_MESSAGES"],
                },
                200,
            ),
            ("schedule", "POST", path + "/schedules", {"interval_minutes": 60}, 201),
        ]
        out = {}
        for name, method, url, payload, expected in steps:
            response = self.client.request(method, url, headers=h, json=payload)
            self.assertEqual(response.status_code, expected, f"{name}: {response.text}")
            out[name] = response.json()
        activated = self.client.post(
            path + "/activate", headers=h, json={"version_id": out["version"]["id"]}
        )
        self.assertEqual(activated.status_code, 200, activated.text)
        self.assertEqual(self.client.get(path, headers=h).json()["status"], "active")
        self.assertIn(
            atom.json()["id"], [a["id"] for a in self.client.get(self.base, headers=h).json()]
        )
        paused = self.client.patch(path, headers=h, json={"status": "paused"})
        self.assertEqual(paused.status_code, 200, paused.text)
        self.assertEqual(paused.json()["status"], "paused")
        killed = self.client.patch(path, headers=h, json={"status": "killed"})
        self.assertEqual(killed.json()["status"], "killed")
        self.assertEqual(
            self.client.patch(path, headers=h, json={"status": "paused"}).status_code, 422
        )
        draft = self.client.post(self.base, headers=h, json={**body, "name": "Scratch"}).json()
        self.assertEqual(
            self.client.delete(f"{self.base}/{draft['id']}", headers=h).status_code, 204
        )

    def test_plain_member_gets_only_their_own_personal_atom(self):
        member = self.member(self.team_a)
        self.addCleanup(self.sql, "delete from atoms where owner_member_id=:id", id=member)
        h = self.headers(member)
        body = {
            "name": "Mine",
            "kind": "personal",
            "owner_member_id": member,
            "model_tier": "small",
            "max_runs_per_day": 999,
            "max_cost_per_day": "99.000000",
            "max_actions_per_day": 999,
        }
        created = self.client.post(self.base, headers=h, json=body)
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(
            created.json()["max_runs_per_day"], 10
        )  # caps forced to the member default
        workspace_atom = self.client.post(
            self.base, headers=h, json={**body, "kind": "workspace", "owner_member_id": None}
        )
        self.assertEqual(workspace_atom.status_code, 403, workspace_atom.text)

    def test_forwarded_token_searches_and_writes_skills_with_the_skill_scopes(self):
        h = self.headers(self.owner, scope="atoms:admin skills:read skills:write")
        written = self.mcp(
            "skill_write",
            {
                "name": "Mail triage",
                "description": "Sort mail",
                "when_to_use": "daily mail",
                "instructions": "Read, then summarize.",
                "scope": "workspace",
            },
            h,
        )
        self.assertFalse(written.get("isError"), written)
        found = self.mcp("skills_search", {"query": "mail"}, h)
        self.assertFalse(found.get("isError"), found)
        self.assertTrue(found["structuredContent"]["skills"])
        no_skill_scope = self.mcp("skills_search", {"query": "mail"}, self.headers(self.owner))
        self.assertTrue(no_skill_scope.get("isError"), no_skill_scope)

    def test_mcp_atom_admin_tools_mirror_the_rest_routes(self):
        h = self.headers(self.owner)
        listed = self.client.post(
            "/mcp",
            headers={**h, "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        ).json()["result"]["tools"]
        names = {tool["name"] for tool in listed}
        self.assertTrue(
            {"atom_create", "atom_activate", "atom_schedule_set", "atom_delete"} <= names
        )

        def call(name, **arguments):
            result = self.mcp(name, arguments, h)
            self.assertFalse(result.get("isError"), (name, result))
            return result["structuredContent"]

        atom = call(
            "atom_create",
            body={
                "name": "Mcp mail",
                "model_tier": "small",
                "max_runs_per_day": 5,
                "max_cost_per_day": "2.000000",
                "max_actions_per_day": 50,
            },
        )
        aid = atom["id"]
        version = call("atom_version_create", atom_id=aid, body={"instructions": "Read mail."})
        call("atom_grant_set", atom_id=aid, body={"resource_type": "memory", "level": "read"})
        connection = call(
            "atom_connection_set",
            atom_id=aid,
            body={
                "toolkit": "outlook",
                "composio_user_id": "u1",
                "toolkit_version": "1.0.0",
                "permission_ceiling": "read",
                "allowed_tools": ["OUTLOOK_LIST_MESSAGES"],
            },
        )
        schedule = call("atom_schedule_set", atom_id=aid, body={"interval_minutes": 60})
        replaced = call(
            "atom_schedule_set",
            atom_id=aid,
            schedule_id=schedule["id"],
            body={"interval_minutes": 30},
        )
        self.assertEqual(replaced["id"], schedule["id"])
        call("atom_activate", atom_id=aid, version_id=version["id"])
        self.assertEqual(call("atom_get", atom_id=aid)["status"], "active")
        self.assertIn(aid, [a["id"] for a in call("atom_list")["atoms"]])
        self.assertEqual(
            call("atom_configure", atom_id=aid, body={"status": "paused"})["status"], "paused"
        )
        call("atom_connection_delete", atom_id=aid, connection_id=connection["id"])
        call("atom_schedule_delete", atom_id=aid, schedule_id=schedule["id"])
        call("atom_configure", atom_id=aid, body={"status": "killed"})
        self.assertEqual(call("atom_delete", atom_id=aid), {"deleted": True})

    def test_mcp_atom_admin_needs_the_admin_scope_and_a_human_token(self):
        weak = self.headers(self.owner, scope="memory:read")
        refused = self.mcp("atom_list", {}, weak)
        self.assertTrue(refused.get("isError"), refused)
        run = self.token(member_id=self.owner, scopes=["atoms:read"])
        self.assertTrue(self.mcp("atom_list", {}, run).get("isError"))

    def test_mcp_admin_attaches_and_toggles_a_skill(self):
        h = self.headers(self.owner, scope="atoms:admin skills:write")
        skill = self.mcp(
            "skill_write",
            {
                "name": "Triage",
                "description": "Sort",
                "when_to_use": "daily",
                "instructions": "Read, then summarize.",
                "scope": "workspace",
            },
            h,
        )["structuredContent"]
        atom = self.mcp(
            "atom_create",
            {
                "body": {
                    "name": "Skilled",
                    "model_tier": "small",
                    "max_runs_per_day": 5,
                    "max_cost_per_day": "2.000000",
                    "max_actions_per_day": 50,
                }
            },
            h,
        )["structuredContent"]
        attached = self.mcp(
            "atom_skill_attach",
            {
                "atom_id": atom["id"],
                "body": {"skill_id": skill["id"], "skill_version": skill["version"]},
            },
            h,
        )
        self.assertFalse(attached.get("isError"), attached)
        attachment = attached["structuredContent"]
        toggled = self.mcp(
            "atom_skill_enable",
            {"atom_id": atom["id"], "attachment_id": attachment["id"], "body": {"enabled": False}},
            h,
        )
        self.assertFalse(toggled.get("isError"), toggled)
        no_scope = self.mcp(
            "atom_skill_attach",
            {
                "atom_id": atom["id"],
                "body": {"skill_id": skill["id"], "skill_version": skill["version"]},
            },
            self.headers(self.owner, scope="skills:write"),
        )
        self.assertTrue(no_scope.get("isError"), no_scope)


class AtomRequestTests(unittest.TestCase):
    def test_schedule_requires_one_cadence_and_aware_time(self):
        for body in (
            {},
            {"cron": "* * * * *", "interval_minutes": 5},
            {"interval_minutes": 4},
            {"interval_minutes": 5, "next_run_at": "2026-01-01"},
        ):
            with self.subTest(body=body), self.assertRaises(ValidationError):
                ScheduleBody(**body)
        self.assertEqual(ScheduleBody(interval_minutes=5).timezone, "UTC")
        ScheduleBody(cron="0 * * * *", next_run_at=datetime.now(UTC))

    def test_connection_requires_explicit_identity_and_pinned_version(self):
        valid = dict(
            toolkit="github",
            composio_user_id="workspace:test",
            toolkit_version="20260101_01",
            permission_ceiling="read",
        )
        for bad in (
            {"toolkit_version": "latest"},
            {"toolkit_version": "LATEST"},
            {"composio_user_id": " "},
            {"status": "active"},
            {"allowed_tools": [""]},
            {"oauth_token": "not-accepted"},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                ConnectionBody(**(valid | bad))
        ConnectionBody(**valid)
        ConnectionBody(**valid, status="active", composio_account_ref="account")

    def test_control_patch_cannot_set_identity_or_activate(self):
        for body in (
            {"status": "active"},
            {"member_id": "anything"},
            {"max_cost_per_day": None},
            {"name": None},
            {"owner_member_id": None},
        ):
            with self.subTest(body=body), self.assertRaises(ValidationError):
                ConfigureAtom(**body)
        self.assertEqual(
            ConfigureAtom(description=None).model_dump(exclude_unset=True), {"description": None}
        )
