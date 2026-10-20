"""Tokens are off by default (D36): the service key is the only credential, and a Bearer
token is ignored with a 401. The rest of the suite runs with tokens on (see harness.py)."""

from unittest.mock import patch

from harness import MemoryHarness

from app.config import settings


class ServiceKeyOnlyTests(MemoryHarness):
    def setUp(self):
        super().setUp()
        switch = patch.object(settings, "AGENT_TOKENS_ENABLED", False)  # the production default
        switch.start()
        self.addCleanup(switch.stop)

    def test_service_key_works_on_crud_and_memory_routes(self):
        service = {"X-Data-API-Key": "test-data-key"}
        self.assertEqual(
            self.client.get(f"/workspaces/{self.team_a}/projects", headers=service).status_code, 200
        )
        self.assertEqual(
            self.client.get(f"/workspaces/{self.team_a}/memories", headers=service).status_code, 200
        )

    def test_bearer_tokens_are_refused(self):
        token, _ = self.minted_before_switch()
        response = self.client.get(
            f"/workspaces/{self.team_a}/projects",
            headers={"Authorization": f"Bearer {token}", "X-Data-API-Key": ""},
        )
        self.assertEqual(response.status_code, 401, response.text)

    def test_minting_is_refused(self):
        response = self.client.post(
            "/auth/tokens",
            json={"workspace_id": self.team_a, "scopes": ["planning:read"]},
        )
        self.assertEqual(response.status_code, 422, response.text)

    def minted_before_switch(self):
        """A real token, minted while tokens are on: it must still be refused."""
        from app.api import tokens

        with patch.object(settings, "AGENT_TOKENS_ENABLED", True):
            return tokens.mint(self.team_a, {"planning:read"}, "x", 60)
