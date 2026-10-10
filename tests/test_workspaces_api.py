from harness import MemoryHarness


class WorkspacesApiTests(MemoryHarness):
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
        response = self.client.get("/workspaces", headers={"X-Data-API-Key": ""})
        self.assertEqual(response.status_code, 401)
