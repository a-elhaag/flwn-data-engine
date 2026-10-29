import unittest

from fastapi.testclient import TestClient

from app.main import app
from app.openapi.business_spec import BUSINESS_TAGS, _refs


class BusinessSpecTests(unittest.TestCase):
    def setUp(self):
        self.spec = TestClient(app).get("/openapi/business.json").json()

    def test_keeps_the_core_business_crud(self):
        paths = self.spec["paths"]
        for path in (
            "/users",
            "/workspaces",
            "/workspaces/{workspace_id}/members",
            "/workspaces/{workspace_id}/teams",
            "/workspaces/{workspace_id}/projects",
            "/workspaces/{workspace_id}/projects/{project_id}/work-items",
            "/workspaces/{workspace_id}/projects/{project_id}/sprints",
            "/workspaces/{workspace_id}/projects/{project_id}/tasks",
        ):
            with self.subTest(path=path):
                self.assertIn(path, paths)

    def test_drops_everything_else(self):
        paths = self.spec["paths"]
        for excluded in (
            "/comments",
            "/channels",
            "/docs",
            "/labels",
            "/memories",
            "/files",
            "/meetings",
            "/atoms",
            "/chunks",
        ):
            with self.subTest(excluded=excluded):
                self.assertFalse(any(excluded in p for p in paths))
        tags = {tag for ops in paths.values() for op in ops.values() for tag in op["tags"]}
        self.assertEqual(tags, BUSINESS_TAGS)
        self.assertEqual({t["name"] for t in self.spec["tags"]}, BUSINESS_TAGS)

    def test_schemas_are_exactly_the_reachable_ones(self):
        schemas = self.spec["components"]["schemas"]
        reachable = _refs(self.spec["paths"])
        for model in schemas.values():
            reachable |= _refs(model)
        self.assertEqual(set(schemas), reachable)
        self.assertLess(len(schemas), len(app.openapi()["components"]["schemas"]))

    def test_does_not_change_the_full_document(self):
        self.assertIn("/workspaces/{workspace_id}/atoms", app.openapi()["paths"])
