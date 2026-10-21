import importlib
import unittest

from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from app.api import examples
from app.api.operations import OPERATIONS
from app.main import app

SCHEMA_MODULES = (
    "app.api.schemas",
    "app.api.atom_schemas",
    "app.api.approval_schemas",
    "app.api.identity_schemas",
    "app.api.planning_schemas",
    "app.api.collab_schemas",
    "app.memory.recall",
    "app.memory.steward",
    "app.decisions.service",
    "app.storage.search",
    "app.storage.files",
    "app.meetings.service",
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
