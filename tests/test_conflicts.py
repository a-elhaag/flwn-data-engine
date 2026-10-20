"""Flagged decision conflicts: agents flag, only human members resolve."""

import json
import unittest

from harness import MemoryHarness

from app.api import tokens

NO_KEY = {"X-Data-API-Key": ""}
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


class ConflictTests(MemoryHarness):
    def setUp(self):
        super().setUp()
        self.agent = self.member(self.team_a, "AI")
        self.human = self.member(self.team_a)
        self.url = f"/workspaces/{self.team_a}/decision-conflicts"

    def headers(self, member, scopes=None):
        token, _ = tokens.mint(self.team_a, scopes or tokens.AGENT_SCOPES, "agent", 600, member)
        return {"Authorization": f"Bearer {token}", **NO_KEY}

    def decision(self) -> str:
        return self.remember(self.team_a)

    def flag(self, decision=None, proposal="Disable the endpoint", headers=None):
        return self.client.post(
            self.url,
            json={
                "decision_id": decision or self.decision(),
                "proposal": proposal,
                "explanation": "It contradicts the decision",
            },
            headers=headers or self.headers(self.agent),
        )

    def test_an_agent_flags_and_the_flag_is_listed_with_its_author_and_audit_row(self):
        response = self.flag()
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual((body["status"], body["flagged_by"]), ("open", self.agent))
        listed = self.client.get(
            self.url, params={"status": "open"}, headers=self.headers(self.agent)
        )
        self.assertEqual([c["id"] for c in listed.json()["items"]], [body["id"]])
        self.assertEqual(
            [e["action"] for e in self.events(self.team_a, "decision")], ["conflict_flagged"]
        )

    def test_flagging_the_same_proposal_again_reuses_the_open_flag(self):
        decision = self.decision()
        first = self.flag(decision).json()["id"]
        self.assertEqual(self.flag(decision).json()["id"], first)
        self.assertEqual(self.flag(decision, proposal="Another action").json()["id"] != first, True)

    def test_flagging_needs_a_real_decision_in_this_workspace(self):
        other = self.remember(self.team_b)
        self.assertEqual(self.flag(other).status_code, 404)

    def test_flagging_needs_the_write_scope(self):
        read_only = self.headers(self.agent, {tokens.SCOPE_CONFLICTS_READ})
        self.assertEqual(self.flag(headers=read_only).status_code, 403)

    def test_an_agent_can_never_resolve_a_flag(self):
        flag = self.flag().json()["id"]
        body = {"status": "dismissed"}
        agent = self.client.put(
            f"{self.url}/{flag}/resolution", json=body, headers=self.headers(self.agent)
        )
        self.assertEqual(agent.status_code, 403)  # tokens never reach service-only routes
        as_agent_member = self.client.put(
            f"{self.url}/{flag}/resolution", json=body, headers={"X-Acting-Member-Id": self.agent}
        )
        self.assertEqual(as_agent_member.status_code, 403)
        no_member = self.client.put(f"{self.url}/{flag}/resolution", json=body)
        self.assertEqual(no_member.status_code, 403)

    def test_a_human_resolves_once(self):
        flag = self.flag().json()["id"]
        as_human = {"X-Acting-Member-Id": self.human}
        response = self.client.put(
            f"{self.url}/{flag}/resolution",
            json={"status": "accepted", "note": "ok"},
            headers=as_human,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            (response.json()["status"], response.json()["resolved_by"]), ("accepted", self.human)
        )
        again = self.client.put(
            f"{self.url}/{flag}/resolution", json={"status": "dismissed"}, headers=as_human
        )
        self.assertEqual(again.status_code, 409)
        self.assertIn(
            "conflict_resolved", [e["action"] for e in self.events(self.team_a, "decision")]
        )

    def test_flags_are_invisible_to_another_workspace(self):
        flag = self.flag().json()["id"]
        other = self.client.get(f"/workspaces/{self.team_b}/decision-conflicts/{flag}")
        self.assertEqual(other.status_code, 404)

    def test_mcp_tools_flag_and_list(self):
        decision = self.decision()
        headers = {**self.headers(self.agent), **MCP_HEADERS}

        def call(name, arguments):
            response = self.client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                },
                headers=headers,
            )
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()["result"]
            self.assertFalse(result.get("isError"), result)
            return json.loads(result["content"][0]["text"])

        flagged = call(
            "conflict_flag",
            {"decision_id": decision, "proposal": "Drop it", "explanation": "Conflicts"},
        )
        self.assertEqual(flagged["status"], "open")
        listed = call("conflicts_list", {"status": "open"})
        self.assertEqual([c["id"] for c in listed["conflicts"]], [flagged["id"]])


if __name__ == "__main__":
    unittest.main()
