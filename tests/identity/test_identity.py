"""Who did it: tokens carry a member, the trusted backend can name one, and writes record it."""

import json
import uuid

import signing
from harness import CrudHarness, MemoryHarness

from app.api import tokens
from app.api import tokens as t

ASSERT_NO_SERVICE_KEY = {"X-Data-API-Key": ""}
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


class IdentityTests(MemoryHarness):
    def agent(self, member=None, workspace=None, scopes=None):
        token, _ = signing.mint(
            workspace or self.team_a, scopes or tokens.AGENT_SCOPES, "agent", 600, member
        )
        return {"Authorization": f"Bearer {token}", **ASSERT_NO_SERVICE_KEY}

    def remember(self, headers=None, workspace=None):
        return self.client.post(
            f"/workspaces/{workspace or self.team_a}/memories",
            json={"text": "t", "source": "chat", "agent": "planner"},
            headers=headers,
        )

    def test_a_token_without_a_member_still_works_and_records_no_author(self):
        memory_id = self.remember(self.agent()).json()["point_id"]
        self.assertIsNone(self.row(memory_id)["created_by"])

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


class IdentityApiTests(CrudHarness):
    def test_member_crud_links_human_profile_to_user(self):
        response = self.client.post(
            f"/workspaces/{self.team_a}/members",
            json={
                "type": "HUMAN",
                "name": "Ada Lovelace",
                "email": "ada@example.com",
                "role": "member",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        member = response.json()
        member_id = member["id"]
        self.assertEqual(
            (member["name"], member["email"], member["type"]),
            ("Ada Lovelace", "ada@example.com", "HUMAN"),
        )
        self.assertIsNotNone(member["user_id"])

        updated = self.client.patch(
            f"/workspaces/{self.team_a}/members/{member_id}",
            json={"name": "Ada Byron", "status": "suspended"},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(
            (updated.json()["name"], updated.json()["status"]), ("Ada Byron", "suspended")
        )
        self.assertEqual(
            self.client.get(f"/workspaces/{self.team_a}/members/{member_id}").status_code, 200
        )
        self.assertEqual(
            self.client.delete(f"/workspaces/{self.team_a}/members/{member_id}").status_code, 204
        )
        self.assertEqual(
            self.client.get(f"/workspaces/{self.team_a}/members/{member_id}").status_code, 404
        )

    def test_duplicate_member_and_foreign_team_member_are_rejected(self):
        member_body = {
            "type": "HUMAN",
            "name": "Ada Lovelace",
            "email": "ada@example.com",
        }
        first = self.client.post(f"/workspaces/{self.team_a}/members", json=member_body)
        self.assertEqual(first.status_code, 201, first.text)
        duplicate = self.client.post(f"/workspaces/{self.team_a}/members", json=member_body)
        self.assertEqual(duplicate.status_code, 409, duplicate.text)

        team = self.client.post(
            f"/workspaces/{self.team_a}/teams", json={"key": "ENG", "name": "Engineering"}
        )
        self.assertEqual(team.status_code, 201, team.text)
        foreign = self.member(self.team_b)
        response = self.client.put(
            f"/workspaces/{self.team_a}/teams/{team.json()['id']}/members",
            json={"member_ids": [foreign]},
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_team_crud_and_member_assignment(self):
        member_id = self.member(self.team_a)
        response = self.client.post(
            f"/workspaces/{self.team_a}/teams",
            json={"key": "ENG", "name": "Engineering", "visibility": "private"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        team_id = response.json()["id"]
        self.assertEqual(response.json()["visibility"], "private")

        assigned = self.client.put(
            f"/workspaces/{self.team_a}/teams/{team_id}/members",
            json={"member_ids": [member_id]},
        )
        self.assertEqual(assigned.status_code, 200, assigned.text)
        self.assertEqual(
            self.sql(
                "select count(*) as n from team_members where workspace_id = :w and team_id = :t",
                w=self.team_a,
                t=team_id,
            )[0]["n"],
            1,
        )
        updated = self.client.patch(
            f"/workspaces/{self.team_a}/teams/{team_id}", json={"name": "Platform"}
        )
        self.assertEqual(updated.json()["name"], "Platform")
        self.assertEqual(
            self.client.delete(f"/workspaces/{self.team_a}/teams/{team_id}").status_code, 204
        )
        self.assertEqual(
            self.client.get(f"/workspaces/{self.team_a}/teams/{team_id}").status_code, 404
        )

    def test_member_routes_require_service_credentials(self):
        response = self.client.get(f"/workspaces/{self.team_a}/members", headers=self.NO_TOKEN)
        self.assertEqual(response.status_code, 401)


class UsersApiTests(CrudHarness):
    def email(self):
        return f"{uuid.uuid4().hex[:10]}@example.com"

    def make(self, **body):
        response = self.client.post("/users", json={"email": self.email(), "name": "Ada", **body})
        self.assertEqual(response.status_code, 201, response.text)
        self.addCleanup(self.sql, "delete from users where id = :i", i=response.json()["id"])
        return response.json()

    def test_user_crud_and_soft_delete(self):
        user = self.make(settings={"theme": "dark"})
        self.assertEqual(user["settings"], {"theme": "dark"})
        updated = self.client.patch(f"/users/{user['id']}", json={"name": "Ada Byron"})
        self.assertEqual((updated.status_code, updated.json()["name"]), (200, "Ada Byron"))
        self.assertIn(user["id"], [u["id"] for u in self.client.get("/users").json()["items"]])
        self.assertEqual(self.client.delete(f"/users/{user['id']}").status_code, 204)
        self.assertEqual(self.client.get(f"/users/{user['id']}").status_code, 404)

    def test_email_is_unique_ignoring_case_and_validated(self):
        user = self.make()
        clash = self.client.post("/users", json={"email": user["email"].upper(), "name": "Other"})
        self.assertEqual(clash.status_code, 409, clash.text)
        bad = self.client.post("/users", json={"email": "not-an-email", "name": "X"})
        self.assertEqual(bad.status_code, 422)
        other = self.make()
        taken = self.client.patch(f"/users/{other['id']}", json={"email": user["email"]})
        self.assertEqual(taken.status_code, 409)
        self.assertEqual(self.client.patch(f"/users/{user['id']}", json={}).status_code, 422)

    def test_user_routes_are_backend_only(self):
        user = self.make()
        admin = self.token_headers(self.team_a, t.SCOPE_ADMIN)
        for method, path in (("get", "/users"), ("get", f"/users/{user['id']}")):
            response = getattr(self.client, method)(path, headers=admin)
            self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(self.client.get("/users", headers=self.NO_TOKEN).status_code, 401)


class WorkspacesApiTests(CrudHarness):
    def test_workspace_crud_and_soft_delete(self):
        created = self.client.post(
            "/workspaces",
            json={"name": "Research", "slug": "research-team", "description": "Initial"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        workspace = created.json()
        workspace_id = workspace["id"]
        self.assertEqual((workspace["name"], workspace["slug"]), ("Research", "research-team"))
        self.assertEqual(workspace["settings"], {})

        self.assertEqual(self.client.get(f"/workspaces/{workspace_id}").json(), workspace)
        self.assertIn(
            workspace_id,
            [item["id"] for item in self.client.get("/workspaces").json()["items"]],
        )

        updated = self.client.patch(
            f"/workspaces/{workspace_id}",
            json={"name": "Research Group", "description": None},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["name"], "Research Group")
        self.assertIsNone(updated.json()["description"])

        self.assertEqual(self.client.delete(f"/workspaces/{workspace_id}").status_code, 204)
        self.assertEqual(self.client.get(f"/workspaces/{workspace_id}").status_code, 404)
        self.assertNotIn(
            workspace_id,
            [item["id"] for item in self.client.get("/workspaces").json()["items"]],
        )
        self.assertEqual(
            [event["action"] for event in self.events(workspace_id, "workspace")],
            ["created", "updated", "deleted"],
        )

    def test_workspace_slug_is_case_insensitively_unique(self):
        self.client.post("/workspaces", json={"name": "A", "slug": "shared-slug"})
        conflict = self.client.post("/workspaces", json={"name": "B", "slug": "SHARED-SLUG"})
        self.assertEqual(conflict.status_code, 409, conflict.text)

    def test_workspace_routes_require_service_credentials(self):
        response = self.client.get("/workspaces", headers=self.NO_TOKEN)
        self.assertEqual(response.status_code, 401)
