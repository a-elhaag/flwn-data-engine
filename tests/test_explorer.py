import unittest

from harness import MemoryHarness

from app.openapi import explorer


class ExplorerMappingTests(unittest.TestCase):
    TABLES = {"projects", "tasks", "members", "work_items", "decision_conflicts", "sprints"}

    def test_an_endpoint_belongs_to_its_last_resource(self):
        cases = {
            "/workspaces/{workspace_id}/projects/{project_id}/tasks": "tasks",
            "/workspaces/{workspace_id}/tasks/by-key/{key}": "tasks",
            "/workspaces/{workspace_id}/members/{member_id}/projects": "projects",
            "/workspaces/{workspace_id}/projects/{project_id}/work-items/{work_item_id}": "work_items",
            "/workspaces/{workspace_id}/decision-conflicts": "decision_conflicts",
        }
        for path, table in cases.items():
            with self.subTest(path=path):
                self.assertEqual(explorer.table_for(path, self.TABLES), table)

    def test_action_routes_without_a_table_are_not_forced_onto_one(self):
        self.assertIsNone(
            explorer.table_for("/workspaces/{workspace_id}/memories/recall", self.TABLES)
        )
        self.assertIsNone(explorer.table_for("/auth/tokens", self.TABLES))


class ExplorerRouteTests(MemoryHarness):
    def test_page_is_served_with_the_schema_and_stays_out_of_the_api_docs(self):
        response = self.client.get("/explorer", headers={"X-Data-API-Key": ""})
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("Endpoints not tied to one table", response.text)
        self.assertNotIn("__DATA__", response.text)
        self.assertNotIn("/explorer", self.client.get("/openapi.json").json()["paths"])

    def test_every_endpoint_on_a_table_names_a_real_table(self):
        spec = self.client.get("/openapi.json").json()
        from app.db import erd

        names = {t["name"] for t in erd.build()["tables"]}
        for row in explorer.operations(spec, names):
            if row["table"] is not None:
                self.assertIn(row["table"], names)
