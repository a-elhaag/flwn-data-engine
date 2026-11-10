"""Every route has one surface (product, raw or internal), and internal routes are service-only (#18)."""

import re
import uuid

from harness import CrudHarness

from app.api import route_kinds as rk
from app.api import tokens as t
from app.main import app


def _fill(path: str) -> str:
    return re.sub(r"\{[^}]+\}", lambda _: str(uuid.uuid4()), path)


class RouteKindTests(CrudHarness):
    def setUp(self):
        super().setUp()
        self.routes = rk.routes_of(app)

    def test_every_route_has_exactly_one_kind(self):
        self.assertTrue(self.routes)
        for method, path, route in self.routes:
            with self.subTest(method=method, path=path):
                self.assertIn(rk.kind_of(method, path, route), rk.KINDS)

    def test_every_listed_internal_route_exists(self):
        found = {(method, path) for method, path, _ in self.routes}
        self.assertEqual(rk.INTERNAL_ROUTES - found, set(), "stale INTERNAL_ROUTES entries")

    def test_internal_routes_are_not_in_the_openapi_document(self):
        paths = app.openapi()["paths"]
        for method, path, route in self.routes:
            if rk.kind_of(method, path, route) != rk.INTERNAL:
                continue
            with self.subTest(method=method, path=path):
                self.assertNotIn(method.lower(), paths.get(path, {}))

    def test_public_routes_are_in_the_openapi_document(self):
        paths = app.openapi()["paths"]
        for method, path, route in self.routes:
            if rk.kind_of(method, path, route) == rk.PRODUCT and route.include_in_schema:
                with self.subTest(method=method, path=path):
                    self.assertIn(method.lower(), paths.get(path, {}))

    def test_a_bearer_token_cannot_reach_an_internal_route(self):
        headers = self.token_headers(self.team_a, t.SCOPE_ADMIN)
        checked = 0
        for method, path, route in self.routes:
            if rk.kind_of(method, path, route) != rk.INTERNAL:
                continue
            if (method, path) in rk.TOKEN_SCOPED_INTERNAL:
                continue  # reachable with its scope by decision (see route_kinds)
            checked += 1
            with self.subTest(method=method, path=path):
                response = self.client.request(method, _fill(path), headers=headers)
                # 403 for allow(None), 401 for service_only: both refuse a token.
                self.assertIn(response.status_code, (401, 403), response.text)
        self.assertGreater(checked, 0)
