import importlib
import unittest

from fastapi.testclient import TestClient
from harness import MemoryHarness
from pydantic import TypeAdapter

from app.main import app
from app.openapi import examples, explorer
from app.openapi.business_spec import BUSINESS_TAGS, _refs
from app.openapi.operations import OPERATIONS

SCHEMA_MODULES = (
    "app.api.schemas.common",
    "app.api.schemas.atoms",
    "app.api.schemas.approvals",
    "app.api.schemas.identity",
    "app.api.schemas.planning",
    "app.api.schemas.collab",
    "app.domain.memory.recall",
    "app.domain.memory.steward",
    "app.domain.decisions.service",
    "app.domain.storage.search",
    "app.domain.storage.files",
    "app.domain.meetings.service",
    "app.api.generic_crud",
)


def _model(name):
    for module in SCHEMA_MODULES:
        found = getattr(importlib.import_module(module), name, None)
        if found:
            return found
    return None


class OpenApiDocsTests(unittest.TestCase):
    def test_examples_validate_against_their_models(self):
        for name, example in examples.EXAMPLES.items():
            with self.subTest(name=name):
                model = _model(name)
                self.assertIsNotNone(model, f"{name} is not a schema")
                TypeAdapter(model).validate_python(example)

    def test_examples_and_tags_reach_the_openapi_document(self):
        spec = app.openapi()
        schemas = spec["components"]["schemas"]
        self.assertEqual(
            schemas["RememberRequest"]["examples"], [examples.EXAMPLES["RememberRequest"]]
        )
        self.assertTrue(spec["info"]["description"])
        for path, operations in spec["paths"].items():
            for method, operation in operations.items():
                with self.subTest(path=path, method=method):
                    self.assertTrue(operation.get("tags"))

    def test_every_field_and_request_body_is_documented(self):
        spec = app.openapi()
        schemas = spec["components"]["schemas"]
        undocumented = [
            f"{name}.{field}"
            for name, model in schemas.items()
            for field, prop in model.get("properties", {}).items()
            if "description" not in prop
        ]
        self.assertEqual(undocumented, [], "add these to app/api/field_docs.py")
        bodies = {
            op["requestBody"]["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
            for ops in spec["paths"].values()
            for op in ops.values()
            if "requestBody" in op
        }
        without_example = sorted(b for b in bodies if "examples" not in schemas[b])
        self.assertEqual(without_example, [], "add these to app/api/examples.py")

    def test_every_endpoint_has_a_hand_written_summary(self):
        routes = {f"{m.upper()} {p}" for p, ops in app.openapi()["paths"].items() for m in ops}
        self.assertEqual(routes - OPERATIONS.keys(), set())
        self.assertEqual(OPERATIONS.keys() - routes, set(), "stale operation keys")

    def test_docs_and_swagger_alias_serve_the_same_collapsed_ui(self):
        client = TestClient(app)
        for path in ("/docs", "/swagger"):
            with self.subTest(path=path):
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn('"docExpansion": "none"', response.text)


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
        self.assertIsNone(explorer.table_for("/auth/revocations", self.TABLES))


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
