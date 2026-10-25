"""Backend-signed tokens (EdDSA, JWKS): claims, audience, revocation, and the legacy HS256 window."""

import json
import time
import unittest
import uuid
from unittest.mock import patch

import env  # noqa: F401  (sets the environment the app needs to import)
import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from harness import TOKEN_SECRET
from jwt.algorithms import OKPAlgorithm
from sqlalchemy.exc import OperationalError

from app.api import revocation, tokens
from app.config import settings

KEY = Ed25519PrivateKey.generate()
OTHER = Ed25519PrivateKey.generate()
JWKS = json.dumps(
    {"keys": [{**OKPAlgorithm.to_jwk(KEY.public_key(), as_dict=True), "kid": "k1", "use": "sig"}]}
)


def ids(count=4):
    return [str(uuid.uuid4()) for _ in range(count)]


class SignedTokenTests(unittest.TestCase):
    def setUp(self):
        self.ws, self.member, self.human, self.run = ids()
        for name, value in (
            ("AGENT_TOKENS_ENABLED", True),
            ("TOKEN_JWKS", JWKS),
            ("TOKEN_ALLOW_LEGACY_HS256", True),
            ("MEMORY_TOKEN_SECRET", TOKEN_SECRET),
        ):
            patcher = patch.object(settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        revoked = patch.object(revocation, "is_revoked", return_value=False)
        self.is_revoked = revoked.start()
        self.addCleanup(revoked.stop)

    def sign(self, key=KEY, kid="k1", **overrides):
        now = int(time.time())
        claims = {
            "iss": settings.TOKEN_ISSUER,
            "aud": [settings.TOKEN_AUDIENCE],
            "sub": "someone",
            "ws": self.ws,
            "typ": "human",
            "ctx": "workspace",
            "mem": self.member,
            "scope": "planning:read",
            "iat": now,
            "exp": now + 900,
            "jti": uuid.uuid4().hex,
        }
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, key, algorithm="EdDSA", headers={"kid": kid})

    def test_each_type_roundtrips_with_its_claims(self):
        cases = {
            "human": {},
            "agent": {"typ": "agent", "obo": self.human},
            "run": {"typ": "run", "agent": self.human, "run": self.run, "scope": "atoms:run"},
            "service": {"typ": "service", "sub": "ai-engine", "mem": None},
        }
        for typ, overrides in cases.items():
            with self.subTest(typ=typ):
                claims = tokens.verify(self.sign(**overrides))
                self.assertEqual((claims.typ, claims.workspace_id), (typ, self.ws))
                self.assertEqual(claims.ctx, "workspace")
                self.assertTrue(claims.jti)
        agent = tokens.verify(self.sign(**cases["agent"]))
        self.assertEqual((agent.member_id, agent.obo), (self.member, self.human))
        run = tokens.verify(self.sign(**cases["run"]))
        self.assertEqual((run.atom_id, run.run_id), (self.human, self.run))

    def test_forwarded_service_is_kept(self):
        self.assertEqual(tokens.verify(self.sign(act="ai-engine")).act, "ai-engine")

    def test_claims_must_fit_the_type(self):
        for overrides in (
            {"obo": self.human},  # a human acts for no one
            {"typ": "agent"},  # an assistant must name its human
            {"typ": "run", "agent": self.human},  # a run needs its run id
            {"typ": "service"},  # a service has no member
            {"typ": "robot"},
            {"ctx": "everywhere"},
            {"mem": None},  # a human needs a member
        ):
            with self.subTest(overrides=overrides), self.assertRaises(tokens.TokenError):
                tokens.verify(self.sign(**overrides))

    def test_missing_claims_are_refused(self):
        for name in ("aud", "jti", "typ", "ctx", "ws", "exp", "iat", "iss"):
            with self.subTest(claim=name), self.assertRaises(tokens.TokenError):
                tokens.verify(self.sign(**{name: None}))

    def test_wrong_audience_issuer_key_or_expiry_is_refused(self):
        for overrides in (
            {"aud": ["atoms-service"]},
            {"iss": "someone-else"},
            {"exp": int(time.time()) - 5},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(tokens.TokenError):
                tokens.verify(self.sign(**overrides))
        with self.assertRaises(tokens.TokenError):  # signed by a key the backend does not own
            tokens.verify(self.sign(key=OTHER))
        with self.assertRaises(tokens.TokenError):  # kid names no known key
            tokens.verify(self.sign(kid="rotated-away"))

    def test_revoked_token_is_refused_and_a_down_database_fails_closed(self):
        self.is_revoked.return_value = True
        with self.assertRaisesRegex(tokens.TokenError, "revoked"):
            tokens.verify(self.sign())
        self.is_revoked.side_effect = OperationalError("select", {}, Exception("down"))
        with self.assertRaisesRegex(tokens.TokenError, "unavailable"):
            tokens.verify(self.sign())

    def test_tokens_off_refuses_everything(self):
        with (
            patch.object(settings, "AGENT_TOKENS_ENABLED", False),
            self.assertRaises(tokens.TokenError),
        ):
            tokens.verify(self.sign())

    def test_legacy_hs256_verifies_until_switched_off(self):
        token, _ = tokens.mint(self.ws, {"memory:read"}, "agent", 60, member_id=self.member)
        claims = tokens.verify(token)
        self.assertEqual((claims.typ, claims.jti), ("agent", None))
        with (
            patch.object(settings, "TOKEN_ALLOW_LEGACY_HS256", False),
            self.assertRaises(tokens.TokenError),
        ):
            tokens.verify(token)

    def test_hs256_cannot_pose_as_a_backend_token(self):
        now = int(time.time())
        forged = jwt.encode(
            {
                "iss": settings.TOKEN_ISSUER,
                "aud": settings.TOKEN_AUDIENCE,
                "sub": "x",
                "ws": self.ws,
                "typ": "human",
                "ctx": "workspace",
                "mem": self.member,
                "jti": "j",
                "iat": now,
                "exp": now + 60,
            },
            TOKEN_SECRET,
            algorithm="HS256",
        )
        with self.assertRaises(tokens.TokenError):  # legacy path wants its own issuer
            tokens.verify(forged)


if __name__ == "__main__":
    unittest.main()
