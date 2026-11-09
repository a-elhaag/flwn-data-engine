"""The error contract: every client error carries a stable code, the same body shape for every
status, and 422 validation errors name the failing fields (issue #27)."""

import ast
import pathlib
import re
import unittest
import uuid

import signing
from harness import MemoryHarness

from app.api import tokens
from app.domain.error_codes import CODES

APP = pathlib.Path(__file__).resolve().parents[2] / "app"
SNAKE = re.compile(r"[a-z][a-z0-9]*(_[a-z0-9]+)*")
# Files that may raise a builtin error: pydantic validators (422 validation_error) and the
# install and upgrade scripts (run by hand, never over HTTP).
UNCODED_OK = ("api/schemas/", "db/install.py", "db/upgrade_atoms.py")


def _python_files(*parts):
    for path in APP.joinpath(*parts).rglob("*.py"):
        yield path, ast.parse(path.read_text(), filename=str(path))


def _literal(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


class ErrorContractTests(MemoryHarness):
    def setUp(self):
        super().setUp()
        self.nobody = str(uuid.uuid4())

    def assertContract(self, response, status, code, fields=None):
        """The one error body: status, a stable code, the English detail and the fields."""
        self.assertEqual(response.status_code, status, response.text)
        body = response.json()
        self.assertEqual(set(body), {"detail", "code", "fields"}, body)
        self.assertEqual(body["code"], code)
        self.assertIn(body["code"], CODES)
        self.assertEqual(body["fields"], fields)
        return body

    def test_404_names_the_resource_in_the_url(self):
        response = self.client.get(f"/workspaces/{self.team_a}/teams/{self.nobody}")
        body = self.assertContract(response, 404, "team_not_found")
        self.assertEqual(body["detail"], "team not found")

    def test_404_for_an_unknown_route_has_a_code_too(self):
        token, _ = signing.mint(self.team_a, tokens.AGENT_SCOPES, "agent", 600, None)
        response = self.client.get(
            "/no-such-route", headers={"Authorization": f"Bearer {token}", "X-Data-API-Key": ""}
        )
        self.assertContract(response, 404, "route_not_found")

    def test_409_names_the_conflict(self):
        user = {"email": f"dup-{uuid.uuid4().hex[:6]}@example.com", "name": "Dup"}
        self.assertEqual(self.client.post("/users", json=user).status_code, 201)
        response = self.client.post("/users", json=user)
        self.assertContract(response, 409, "email_taken")

    def test_422_validation_lists_each_bad_field(self):
        response = self.client.post("/users", json={})
        body = self.assertContract(
            response,
            422,
            "validation_error",
            {"email": "Field required", "name": "Field required"},
        )
        self.assertIsInstance(body["detail"], list)  # as before: one entry per problem

    def test_422_names_a_bad_header_id(self):
        response = self.client.post(
            f"/workspaces/{self.team_a}/memories",
            json={"text": "t", "source": "chat", "agent": "planner"},
            headers={"X-Acting-Member-Id": self.nobody},
        )
        body = self.assertContract(
            response,
            422,
            "acting_member_not_found",
            {"X-Acting-Member-Id": "not an active member of this workspace"},
        )
        self.assertIn("not active", body["detail"])

    def test_403_when_the_token_lacks_the_scope(self):
        token, _ = signing.mint(self.team_a, frozenset(), "agent", 600, None)
        response = self.client.get(
            f"/workspaces/{self.team_a}/teams",
            headers={"Authorization": f"Bearer {token}", "X-Data-API-Key": ""},
        )
        self.assertContract(response, 403, "scope_missing")

    def test_401_for_a_wrong_service_key(self):
        response = self.client.get(
            f"/workspaces/{self.team_a}/teams", headers={"X-Data-API-Key": "wrong"}
        )
        self.assertContract(response, 401, "service_key_invalid")

    def test_every_error_response_carries_a_code(self):
        token, _ = signing.mint(self.team_a, tokens.AGENT_SCOPES, "agent", 600, None)
        bearer = {"Authorization": f"Bearer {token}", "X-Data-API-Key": ""}
        requests = [
            self.client.get("/no-such-route"),  # no token: 401 before the route is looked up
            self.client.get("/no-such-route", headers=bearer),
            self.client.post("/healthz", headers=bearer),
            self.client.post("/users", json={}),
            self.client.get(f"/workspaces/{self.team_a}/teams/{self.nobody}"),
            self.client.get("/users", headers={"X-Data-API-Key": "wrong"}),
        ]
        for response in requests:
            with self.subTest(status=response.status_code, url=str(response.url)):
                self.assertGreaterEqual(response.status_code, 400)
                body = response.json()
                self.assertIsInstance(body.get("code"), str)
                self.assertTrue(body["code"])


class CodeListTests(unittest.TestCase):
    def test_codes_are_unique_and_snake_case(self):
        self.assertEqual(len(CODES), len(set(CODES)), "a code is listed twice")
        for code in CODES:
            with self.subTest(code=code):
                self.assertRegex(code, SNAKE)

    def test_every_code_in_the_source_is_listed(self):
        used = set()
        for _, tree in _python_files():
            for node in ast.walk(tree):
                if isinstance(node, ast.keyword) and node.arg == "code":
                    used.add(_literal(node.value))
                elif isinstance(node, ast.ClassDef):
                    for stmt in node.body:
                        if isinstance(stmt, ast.Assign) and any(
                            isinstance(t, ast.Name) and t.id == "code" for t in stmt.targets
                        ):
                            used.add(_literal(stmt.value))
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    if node.func.id == "api_error" and len(node.args) >= 2:
                        used.add(_literal(node.args[1]))
                    if node.func.id == "code_of" and len(node.args) >= 2:
                        used.add(_literal(node.args[1]))
        used.discard(None)  # dynamic codes (f-strings) are listed by hand
        self.assertEqual(sorted(used - set(CODES)), [])

    def test_no_raise_produces_an_uncoded_error(self):
        """Domain and API code raise DomainError subclasses or api_error, never a builtin."""
        problems = []
        for group in (_python_files("domain"), _python_files("api"), _python_files("db")):
            for path, tree in group:
                rel = path.relative_to(APP).as_posix()
                if rel.startswith(UNCODED_OK):
                    continue
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Raise) or node.exc is None:
                        continue
                    exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
                    if isinstance(exc, ast.Name) and exc.id in (
                        "ValueError",
                        "PermissionError",
                        "LookupError",
                    ):
                        problems.append(f"{rel}:{node.lineno} raises builtin {exc.id}")
                    if (
                        isinstance(node.exc, ast.Call)
                        and isinstance(node.exc.func, ast.Name)
                        and node.exc.func.id == "HTTPException"
                        and rel != "api/errors.py"
                    ):
                        problems.append(f"{rel}:{node.lineno} raises HTTPException, use api_error")
        self.assertEqual(problems, [])

    def test_every_code_is_documented_in_the_openapi_description(self):
        from app.main import app

        description = app.openapi()["info"]["description"]
        for code in CODES:
            with self.subTest(code=code):
                self.assertIn(f"`{code}`", description)
