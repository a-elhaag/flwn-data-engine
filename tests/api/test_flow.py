"""The whole journey in one test, through real tokens: the pieces work together.

Ported in spirit from the feature branch's test_flow (organization -> workspace, header -> token)."""

import signing
from harness import CrudHarness

from app.api import tokens as t


class FullJourneyTest(CrudHarness):
    def test_account_to_finished_work(self):
        # backend: an account, a workspace, its first (owner) member
        user = self.client.post("/users", json={"email": "flow@example.com", "name": "Flo"})
        self.assertEqual(user.status_code, 201, user.text)
        self.addCleanup(self.sql, "delete from users where id = :i", i=user.json()["id"])
        workspace = self.client.post("/workspaces", json={"name": "Flow", "slug": "flow-journey"})
        self.assertEqual(workspace.status_code, 201, workspace.text)
        ws = workspace.json()["id"]
        self.addCleanup(self.sql, "delete from workspaces where id = :i", i=ws)
        self.addCleanup(self.sql, "delete from members where workspace_id = :i", i=ws)
        (owner,) = self.sql(
            "insert into members (workspace_id, user_id, type, role) values (:w, :u, 'HUMAN', 'owner')"
            " returning id",
            w=ws,
            u=user.json()["id"],
        )

        # the backend signs the owner's token; from here on it is all the owner
        token, _ = signing.mint(ws, {t.SCOPE_ADMIN}, "owner", 600, str(owner["id"]))
        me = {"Authorization": f"Bearer {token}", "X-Data-API-Key": ""}
        base = f"/workspaces/{ws}"

        def call(method, path, expect, **kwargs):
            response = getattr(self.client, method)(base + path, headers=me, **kwargs)
            self.assertEqual(response.status_code, expect, f"{method} {path}: {response.text}")
            return response.json() if response.content else None

        # a team with the owner on it, a project, a sprint, a work item, three tasks
        team = call("post", "/teams", 201, json={"key": "BE", "name": "Backend"})
        call("put", f"/teams/{team['id']}/members", 200, json={"member_ids": [str(owner["id"])]})
        project = call(
            "post", "/projects", 201, json={"key": "FLWN", "name": "Flwn", "team_id": team["id"]}
        )
        pid = f"/projects/{project['id']}"
        sprint = call("post", f"{pid}/sprints", 201, json={"name": "Sprint 1", "status": "active"})
        item = call("post", f"{pid}/work-items", 201, json={"title": "Login", "type": "FEATURE"})
        tasks = [
            call(
                "post",
                f"{pid}/tasks",
                201,
                json={
                    "work_item_id": item["id"],
                    "title": f"t{n}",
                    **({"sprint_id": sprint["id"]} if n == 1 else {}),
                },
            )
            for n in (1, 2, 3)
        ]
        self.assertEqual(
            [(x["number"], x["identifier"]) for x in tasks],
            [(1, "FLWN-1"), (2, "FLWN-2"), (3, "FLWN-3")],
        )
        self.assertTrue(all(x["created_by"] == str(owner["id"]) for x in tasks))

        # work moves; deleting the sprint returns its task to the backlog, and keeps it
        call("patch", f"{pid}/tasks/{tasks[1]['id']}", 200, json={"status": "in_progress"})
        call("delete", f"{pid}/sprints/{sprint['id']}", 204)
        self.assertIsNone(call("get", f"{pid}/tasks/{tasks[0]['id']}", 200)["sprint_id"])

        # discussion on the project, authored by the token's member
        comment = call(
            "post",
            "/comments",
            201,
            json={"target_type": "project", "target_id": project["id"], "body": "kickoff"},
        )
        self.assertEqual(comment["author_id"], str(owner["id"]))

        # deleting the project hides it and everything under it
        call("delete", pid, 204)
        call("get", pid, 404)
        call("get", f"{pid}/tasks/{tasks[2]['id']}", 404)
        listed = call("get", "/projects", 200)
        self.assertNotIn(project["id"], [p["id"] for p in listed["items"]])
