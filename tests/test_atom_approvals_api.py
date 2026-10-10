"""REST/MCP approval flow using the existing restricted-role HTTP fixture."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import test_atoms_api as fixtures

from app.atoms.service import AtomService


class ApprovalApiTests(fixtures.AtomApiTests):
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
        with patch("app.atoms.approvals.datetime") as clock:
            clock.now.return_value = expires + timedelta(seconds=1)
            response = self.client.post(path + "/claim", headers=token)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("expired", response.text)
