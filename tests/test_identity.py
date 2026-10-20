"""Who did it: tokens carry a member, the trusted backend can name one, and writes record it."""

import json
import unittest

from harness import MemoryHarness

from app.api import tokens

ASSERT_NO_SERVICE_KEY = {"X-Data-API-Key": ""}
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


class IdentityTests(MemoryHarness):
    def agent(self, member=None, workspace=None, scopes=None):
        token, _ = tokens.mint(
            workspace or self.team_a, scopes or tokens.AGENT_SCOPES, "agent", 600, member
        )
        return {"Authorization": f"Bearer {token}", **ASSERT_NO_SERVICE_KEY}

    def remember(self, headers=None, workspace=None):
        return self.client.post(
            f"/workspaces/{workspace or self.team_a}/memories",
            json={"text": "t", "source": "chat", "agent": "planner"},
            headers=headers,
        )

    # -- minting ----------------------------------------------------------------------------

    def test_a_token_is_minted_for_an_active_member_of_that_workspace(self):
        ana = self.member(self.team_a)
        body = {"workspace_id": self.team_a, "member_id": ana, "ttl_seconds": 60}
        response = self.client.post("/auth/tokens", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(tokens.verify(response.json()["token"]).member_id, ana)

    def test_minting_refuses_a_member_who_is_missing_foreign_or_suspended(self):
        elsewhere = self.member(self.team_b)
        suspended = self.member(self.team_a, status="suspended")
        for member in (elsewhere, suspended, "00000000-0000-4000-8000-000000000000"):
            body = {"workspace_id": self.team_a, "member_id": member}
            self.assertEqual(self.client.post("/auth/tokens", json=body).status_code, 422, member)
        body = {"workspace_id": self.team_a, "member_id": "not-a-uuid"}
        self.assertEqual(self.client.post("/auth/tokens", json=body).status_code, 422)

    def test_a_token_without_a_member_still_works_and_records_no_author(self):
        memory_id = self.remember(self.agent()).json()["point_id"]
        self.assertIsNone(self.row(memory_id)["created_by"])

    # -- authorship ---------------------------------------------------------------------------

    def test_a_memory_records_the_member_in_the_token(self):
        ghost = self.member(self.team_a, kind="AI")
        memory_id = self.remember(self.agent(ghost)).json()["point_id"]
        self.assertEqual(str(self.row(memory_id)["created_by"]), ghost)
        (event,) = [e for e in self.events(self.team_a, "memory") if e["action"] == "created"]
        self.assertEqual((str(event["actor_id"]), str(event["entity_id"])), (ghost, memory_id))

    def test_the_trusted_backend_can_name_the_human_it_acts_for(self):
        ana = self.member(self.team_a)
        response = self.remember(headers={"X-Acting-Member-Id": ana})
        memory_id = response.json()["point_id"]
        self.assertEqual(str(self.row(memory_id)["created_by"]), ana)

    def test_the_service_cannot_name_a_member_who_is_not_active_here(self):
        for member in (
            self.member(self.team_b),
            self.member(self.team_a, status="suspended"),
            "junk",
        ):
            response = self.remember(headers={"X-Acting-Member-Id": member})
            self.assertEqual(response.status_code, 422, member)
        self.assertEqual(self.count(self.team_a), 0)  # nothing was written

    def test_an_agent_cannot_act_as_someone_else_by_sending_the_header(self):
        ghost, ana = self.member(self.team_a, kind="AI"), self.member(self.team_a)
        headers = {**self.agent(ghost), "X-Acting-Member-Id": ana}
        memory_id = self.remember(headers).json()["point_id"]
        self.assertEqual(str(self.row(memory_id)["created_by"]), ghost)  # the signed token wins

    def test_a_member_from_another_workspace_cannot_be_smuggled_into_a_token(self):
        foreign = self.member(self.team_b)
        # a token is only minted through the API, which checks; a forged claim fails the live check
        headers = self.agent(foreign)  # signed directly, bypassing the mint check
        self.assertEqual(self.remember(headers).status_code, 403)
        self.assertEqual(self.count(self.team_a), 0)

    # -- revocation ---------------------------------------------------------------------------

    def test_suspending_a_member_stops_their_existing_token_at_once(self):
        ghost = self.member(self.team_a, kind="AI")
        headers = self.agent(ghost)
        self.assertEqual(self.remember(headers).status_code, 200)
        self.sql("update members set status = 'suspended' where id = :i", i=ghost)
        refused = self.remember(headers)
        self.assertEqual(
            (refused.status_code, refused.json()["detail"]),
            (403, "Member is not active in this workspace"),
        )
        self.sql("update members set status = 'active' where id = :i", i=ghost)
        self.assertEqual(self.remember(headers).status_code, 200)  # reactivated: works again

    def test_a_removed_member_stops_working_too(self):
        ghost = self.member(self.team_a, kind="AI")
        headers = self.agent(ghost)
        self.sql("update members set deleted_at = now() where id = :i", i=ghost)
        self.assertEqual(self.remember(headers).status_code, 403)

    # -- the audit log ------------------------------------------------------------------------

    def test_every_memory_change_leaves_an_event_with_its_actor(self):
        ana = self.member(self.team_a)
        headers = {"X-Acting-Member-Id": ana}
        base = f"/workspaces/{self.team_a}/memories"
        memory_id = self.remember(headers).json()["point_id"]
        self.client.patch(f"{base}/{memory_id}", json={"text": "better"}, headers=headers)
        self.client.put(f"{base}/{memory_id}/pin", json={"pinned": True}, headers=headers)
        self.client.put(f"{base}/{memory_id}/pin", json={"pinned": False}, headers=headers)
        self.client.delete(f"{base}/{memory_id}", headers=headers)
        self.client.delete(f"{base}/{memory_id}", headers=headers)  # already gone: no second event
        events = self.events(self.team_a, "memory")
        self.assertEqual(
            [e["action"] for e in events], ["created", "updated", "pinned", "unpinned", "deleted"]
        )
        self.assertEqual({str(e["actor_id"]) for e in events}, {ana})

    def test_a_purge_and_a_sweep_are_recorded(self):
        old = self.remember().json()["point_id"]
        self.age(old, 60)
        self.client.post(f"/workspaces/{self.team_a}/memories/cleanup", json={})
        self.remember()
        self.client.delete(f"/workspaces/{self.team_a}/memories?confirm={self.team_a}")
        actions = [e["action"] for e in self.events(self.team_a, "workspace")]
        self.assertEqual(actions, ["memories_swept", "memories_purged"])

    def test_events_stay_inside_their_workspace(self):
        self.remember()
        self.remember(workspace=self.team_b)
        self.assertEqual(len(self.events(self.team_a)), 1)
        self.assertEqual(len(self.events(self.team_b)), 1)

    def test_a_failed_write_leaves_no_event(self):
        ghost = str(__import__("uuid").uuid4())
        self.remember(workspace=ghost)  # unknown workspace: 404
        self.assertEqual(self.events(self.team_a), [])

    # -- MCP ----------------------------------------------------------------------------------

    def call(self, tool, arguments, headers):
        response = self.client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            },
            headers={**MCP_HEADERS, **headers},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["result"]

    def test_mcp_tools_act_as_the_token_member_and_refuse_a_suspended_one(self):
        ghost = self.member(self.team_a, kind="AI")
        headers = self.agent(ghost)
        result = self.call(
            "memory_remember", {"text": "t", "source": "chat", "agent": "p"}, headers
        )
        memory_id = json.loads(result["content"][0]["text"])["id"]
        self.assertEqual(str(self.row(memory_id)["created_by"]), ghost)
        self.sql("update members set status = 'suspended' where id = :i", i=ghost)
        refused = self.call("memory_recall", {"query": "q", "agent": "p"}, headers)
        self.assertTrue(refused["isError"])


if __name__ == "__main__":
    unittest.main()
