"""A backend-signed token through the real gate: workspace lock, revocation, assistant and human."""

import time
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import jwt
from harness import MemoryHarness
from test_signed_tokens import JWKS, KEY

from app.api import revocation
from app.config import settings

NO_SERVICE_KEY = {"X-Data-API-Key": ""}  # the harness client sends the service key by default


class SignedTokenGateTests(MemoryHarness):
    def setUp(self):
        super().setUp()
        patcher = patch.object(settings, "TOKEN_JWKS", JWKS)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.human = self.member(self.team_a)

    def token(self, workspace=None, **overrides) -> tuple[str, str]:
        now = int(time.time())
        jti = uuid.uuid4().hex
        claims = {
            "iss": settings.TOKEN_ISSUER,
            "aud": [settings.TOKEN_AUDIENCE],
            "sub": "someone",
            "ws": workspace or self.team_a,
            "typ": "human",
            "ctx": "workspace",
            "mem": self.human,
            "scope": "memory:read",
            "iat": now,
            "exp": now + 900,
            "jti": jti,
        }
        claims.update(overrides)
        return jwt.encode(claims, KEY, algorithm="EdDSA", headers={"kid": "k1"}), jti

    def get(self, token, workspace=None):
        return self.client.get(
            f"/workspaces/{workspace or self.team_a}/memories",
            headers={**NO_SERVICE_KEY, "Authorization": f"Bearer {token}"},
        )

    def test_token_works_in_its_workspace_only(self):
        token, _ = self.token()
        self.assertEqual(self.get(token).status_code, 200)
        self.assertEqual(self.get(token, self.team_b).status_code, 403)

    def test_missing_scope_is_refused(self):
        token, _ = self.token(scope="planning:read")
        self.assertEqual(self.get(token).status_code, 403)

    def test_revoked_token_stops_at_once_and_only_the_backend_can_revoke(self):
        token, jti = self.token()
        body = {"jti": jti, "expires_at": int(time.time()) + 900}
        self.assertEqual(self.client.post("/auth/revocations", json=body).status_code, 204)
        self.assertEqual(self.client.post("/auth/revocations", json=body).status_code, 204)
        self.assertEqual(self.get(token).status_code, 401)
        other, _ = self.token()
        self.assertEqual(self.get(other).status_code, 200)
        denied = self.client.post("/auth/revocations", json=body, headers=NO_SERVICE_KEY)
        self.assertEqual(denied.status_code, 401)

    def test_expired_entries_are_dropped_on_the_next_revoke(self):
        revocation.revoke("old", int((datetime.now(UTC) - timedelta(hours=1)).timestamp()))
        revocation.revoke("new", int(time.time()) + 900)
        self.assertFalse(revocation.is_revoked("old"))
        self.assertTrue(revocation.is_revoked("new"))

    def test_assistant_stops_when_the_human_it_acts_for_is_suspended(self):
        assistant = self.member(self.team_a)
        owner = self.member(self.team_a)
        token, _ = self.token(typ="agent", mem=assistant, obo=owner)
        self.assertEqual(self.get(token).status_code, 200)
        self.sql("update members set status = 'suspended' where id = :i", i=owner)
        self.assertEqual(self.get(token).status_code, 403)
