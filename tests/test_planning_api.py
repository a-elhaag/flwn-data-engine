from harness import MemoryHarness


class PlanningApiTests(MemoryHarness):
    def project(self, workspace, key="ENG"):
        response = self.client.post(
            f"/workspaces/{workspace}/projects", json={"key": key, "name": "Engineering"}
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def work_item(self, workspace, project_id):
        response = self.client.post(
            f"/workspaces/{workspace}/projects/{project_id}/work-items",
            json={"title": "API", "type": "FEATURE"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_project_work_item_task_lifecycle_and_numbering(self):
        project = self.project(self.team_a)
        self.assertEqual(project["task_counter"], 0)
        self.assertEqual(
            self.sql(
                "select count(*) as n from backlogs where workspace_id = :w and project_id = :p",
                w=self.team_a,
                p=project["id"],
            )[0]["n"],
            1,
        )

        item = self.work_item(self.team_a, project["id"])
        created = self.client.post(
            f"/workspaces/{self.team_a}/projects/{project['id']}/tasks",
            json={"work_item_id": item["id"], "title": "Add endpoint", "priority": "high"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        task = created.json()
        self.assertEqual((task["number"], task["identifier"]), (1, "ENG-1"))
        self.assertEqual(task["status"], "backlog")

        updated = self.client.patch(
            f"/workspaces/{self.team_a}/projects/{project['id']}/tasks/{task['id']}",
            json={"status": "in_progress", "estimate": 5},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual((updated.json()["status"], updated.json()["estimate"]), ("in_progress", 5))
        listed = self.client.get(f"/workspaces/{self.team_a}/projects/{project['id']}/tasks")
        self.assertEqual([row["id"] for row in listed.json()["items"]], [task["id"]])

        self.assertEqual(
            self.client.delete(
                f"/workspaces/{self.team_a}/projects/{project['id']}/work-items/{item['id']}"
            ).status_code,
            204,
        )
        self.assertEqual(
            self.client.get(
                f"/workspaces/{self.team_a}/projects/{project['id']}/tasks/{task['id']}"
            ).status_code,
            404,
        )

    def test_task_rejects_work_item_from_another_project(self):
        project_a = self.project(self.team_a, "ALPHA")
        project_b = self.project(self.team_a, "BETA")
        item_b = self.work_item(self.team_a, project_b["id"])
        response = self.client.post(
            f"/workspaces/{self.team_a}/projects/{project_a['id']}/tasks",
            json={"work_item_id": item_b["id"], "title": "Invalid association"},
        )
        self.assertEqual(response.status_code, 404, response.text)

    def test_project_and_task_routes_require_service_credentials(self):
        response = self.client.get(
            f"/workspaces/{self.team_a}/projects", headers={"X-Data-API-Key": ""}
        )
        self.assertEqual(response.status_code, 401)

    def sprint(self, workspace, project_id, **body):
        response = self.client.post(
            f"/workspaces/{workspace}/projects/{project_id}/sprints",
            json={"name": "Sprint 1", **body},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_sprint_lifecycle_and_delete_returns_tasks_to_backlog(self):
        project = self.project(self.team_a)
        sprint = self.sprint(self.team_a, project["id"], goal="Ship it")
        self.assertEqual((sprint["status"], sprint["goal"]), ("planned", "Ship it"))
        base = f"/workspaces/{self.team_a}/projects/{project['id']}"

        updated = self.client.patch(
            f"{base}/sprints/{sprint['id']}",
            json={"status": "active", "start_date": "2026-01-05", "end_date": "2026-01-19"},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["status"], "active")
        listed = self.client.get(f"{base}/sprints", params={"status": "active"})
        self.assertEqual([row["id"] for row in listed.json()["items"]], [sprint["id"]])
        none = self.client.get(f"{base}/sprints", params={"status": "completed"})
        self.assertEqual(none.json()["items"], [])

        item = self.work_item(self.team_a, project["id"])
        task = self.client.post(
            f"{base}/tasks",
            json={"work_item_id": item["id"], "title": "In sprint", "sprint_id": sprint["id"]},
        ).json()
        self.assertEqual(task["sprint_id"], sprint["id"])

        self.assertEqual(self.client.delete(f"{base}/sprints/{sprint['id']}").status_code, 204)
        self.assertEqual(self.client.get(f"{base}/sprints/{sprint['id']}").status_code, 404)
        kept = self.client.get(f"{base}/tasks/{task['id']}")
        self.assertEqual(kept.status_code, 200, kept.text)
        self.assertIsNone(kept.json()["sprint_id"])

    def test_sprint_rejects_bad_dates_and_status(self):
        project = self.project(self.team_a)
        url = f"/workspaces/{self.team_a}/projects/{project['id']}/sprints"
        for body in (
            {"name": "S", "start_date": "2026-02-02", "end_date": "2026-02-01"},
            {"name": "S", "status": "done"},
            {"name": "  "},
        ):
            self.assertEqual(self.client.post(url, json=body).status_code, 422, body)

    def test_sprint_is_isolated_by_workspace_and_project(self):
        project_a = self.project(self.team_a, "ALPHA")
        project_b = self.project(self.team_a, "BETA")
        sprint = self.sprint(self.team_a, project_a["id"])
        wrong_project = (
            f"/workspaces/{self.team_a}/projects/{project_b['id']}/sprints/{sprint['id']}"
        )
        self.assertEqual(self.client.get(wrong_project).status_code, 404)
        other_workspace = (
            f"/workspaces/{self.team_b}/projects/{project_a['id']}/sprints/{sprint['id']}"
        )
        self.assertEqual(self.client.get(other_workspace).status_code, 404)

    def test_sprint_routes_require_service_credentials(self):
        project = self.project(self.team_a)
        response = self.client.get(
            f"/workspaces/{self.team_a}/projects/{project['id']}/sprints",
            headers={"X-Data-API-Key": ""},
        )
        self.assertEqual(response.status_code, 401)
