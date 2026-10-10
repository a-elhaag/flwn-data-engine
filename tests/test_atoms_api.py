import json
from datetime import UTC, datetime
from unittest.mock import patch

from harness import MemoryHarness
from sqlalchemy import create_engine, event

from app.db.install import grant_app_role


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

    def token(self, **body):
        response = self.client.post(
            "/auth/tokens", json={"workspace_id": self.team_a, "ttl_seconds": 600, **body}
        )
        self.assertEqual(response.status_code, 200, response.text)
        return {"X-Data-API-Key": "", "Authorization": "Bearer " + response.json()["token"]}

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
        headers = self.token(
            member_id=atom["member_id"],
            atom_id=atom["id"],
            run_id=run["id"],
            scopes=["atoms:read", "atoms:run", "atoms:propose"],
        )
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
            "cost": "0.01",
            "cursor": {"page": 2},
        }
        for _ in range(2):
            result = self.mcp("atom_run_finish", finished, headers)
            self.assertFalse(result.get("isError"), result)
        row = self.sql("select cost, tokens_in from agent_runs where id=:id", id=run["id"])[0]
        self.assertEqual(str(row["cost"]), "0.010000")
        self.assertEqual(row["tokens_in"], 12)
        denied_load = self.mcp("atom_load", {}, headers)
        self.assertTrue(denied_load.get("isError"), denied_load)
