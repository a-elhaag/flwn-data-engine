"""A row of one workspace must look missing to every other workspace, and a token must not cross.

Ported in spirit from the feature branch's cross-cutting tests (organization -> workspace).
"""

import uuid

from harness import CrudHarness

from app.api import tokens as t


class TenantIsolationTests(CrudHarness):
    def post(self, path, body):
        response = self.client.post(f"/workspaces/{self.team_a}{path}", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def make(self):
        """(name, path inside the workspace, a PATCH body) for one row of every kind."""
        project = self.post("/projects", {"key": "ISO", "name": "Iso"})
        item = self.post(f"/projects/{project}/work-items", {"title": "w", "type": "FEATURE"})
        base = f"/projects/{project}"
        return [
            (
                "member",
                "/members/"
                + self.post(
                    "/members",
                    {
                        "type": "HUMAN",
                        "name": "Ada",
                        "email": f"{uuid.uuid4().hex[:8]}@example.com",
                    },
                ),
                {"name": "Renamed"},
            ),
            ("team", "/teams/" + self.post("/teams", {"key": "ISO", "name": "T"}), {"name": "R"}),
            ("project", base, {"name": "Renamed"}),
            ("work item", f"{base}/work-items/{item}", {"title": "Renamed"}),
            (
                "sprint",
                f"{base}/sprints/" + self.post(f"{base}/sprints", {"name": "S"}),
                {"name": "R"},
            ),
            (
                "task",
                f"{base}/tasks/" + self.post(f"{base}/tasks", {"work_item_id": item, "title": "t"}),
                {"title": "Renamed"},
            ),
            ("doc", "/docs/" + self.post("/docs", {"title": "D"}), {"title": "Renamed"}),
            (
                "channel",
                "/channels/" + self.post("/channels", {"type": "group", "name": "c"}),
                {"name": "Renamed"},
            ),
        ]

    def test_other_workspace_sees_every_row_as_missing(self):
        mine = self.token_headers(self.team_a, t.SCOPE_ADMIN, member=self.admin_member(self.team_a))
        theirs = self.token_headers(
            self.team_b, t.SCOPE_ADMIN, member=self.admin_member(self.team_b)
        )
        for name, path, patch in self.make():
            with self.subTest(row=name):
                own, other = f"/workspaces/{self.team_a}{path}", f"/workspaces/{self.team_b}{path}"
                self.assertEqual(self.client.get(own, headers=mine).status_code, 200, own)
                # B's own workspace has no such row ...
                self.assertEqual(self.client.get(other, headers=theirs).status_code, 404, other)
                self.assertEqual(
                    self.client.patch(other, json=patch, headers=theirs).status_code, 404
                )
                self.assertEqual(self.client.delete(other, headers=theirs).status_code, 404)
                # ... and B's token cannot name A's workspace in the URL.
                for call in (self.client.get, self.client.delete):
                    self.assertEqual(call(own, headers=theirs).status_code, 403, own)
                self.assertEqual(
                    self.client.patch(own, json=patch, headers=theirs).status_code, 403
                )
                self.assertEqual(self.client.get(own, headers=mine).status_code, 200, "untouched")

    def test_service_key_and_wrong_secret_tokens_never_reach_a_workspace(self):
        path = f"/workspaces/{self.team_a}/projects"
        service = {"X-Data-API-Key": "test-data-key", "Authorization": ""}
        self.assertEqual(self.client.get(path, headers=service).status_code, 200)
        forged = {"Authorization": "Bearer not.a.token", "X-Data-API-Key": ""}
        self.assertEqual(self.client.get(path, headers=forged).status_code, 401)
