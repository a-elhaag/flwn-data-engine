"""The auth store: hashes only, service key only, single-use codes, rotation with reuse detection."""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import signing
from harness import MemoryHarness

from app.api import tokens

FUTURE = (datetime.now(UTC) + timedelta(days=30)).isoformat()
PAST = (datetime.now(UTC) - timedelta(days=1)).isoformat()


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class AuthStoreTests(MemoryHarness):
    def setUp(self):
        super().setUp()
        self.email = f"{uuid.uuid4().hex[:10]}@example.com"
        self.user = self.client.post("/users", json={"email": self.email, "name": "Ada"}).json()
        self.user_id = self.user["id"]

    def call(self, method, path, expected, **kwargs):
        response = self.client.request(method, path, **kwargs)
        self.assertEqual(response.status_code, expected, response.text)
        return response

    def error_code(self, method, path, status, **kwargs):
        response = self.call(method, path, status, **kwargs)
        return response.json()["code"]

    def new_session(self, refresh, expires=FUTURE):
        body = {"user_id": self.user_id, "refresh_hash": digest(refresh), "expires_at": expires}
        return self.call("POST", "/auth-store/sessions", 201, json=body).json()["id"]

    def find(self, refresh):
        return self.client.get("/auth-store/sessions", params={"refresh_hash": digest(refresh)})

    def test_credentials_put_then_get_by_email_in_any_case(self):
        self.call(
            "PUT", f"/auth-store/credentials/{self.user_id}", 204, json={"password_hash": "argon"}
        )
        found = self.call(
            "GET", "/auth-store/credentials", 200, params={"email": self.email.upper()}
        )
        self.assertEqual(found.json(), {"user_id": self.user_id, "password_hash": "argon"})
        self.call(
            "PUT", f"/auth-store/credentials/{self.user_id}", 204, json={"password_hash": "new"}
        )
        self.assertEqual(
            self.client.get("/auth-store/credentials", params={"email": self.email}).json()[
                "password_hash"
            ],
            "new",
        )

    def test_deleting_a_credential_is_idempotent(self):
        self.call(
            "PUT", f"/auth-store/credentials/{self.user_id}", 204, json={"password_hash": "argon"}
        )
        self.call("DELETE", f"/auth-store/credentials/{self.user_id}", 204)
        self.assertEqual(
            self.error_code("GET", "/auth-store/credentials", 404, params={"email": self.email}),
            "credential_not_found",
        )
        self.call("DELETE", f"/auth-store/credentials/{self.user_id}", 204)

    def test_credential_404s(self):
        unknown = str(uuid.uuid4())
        self.assertEqual(
            self.error_code(
                "PUT", f"/auth-store/credentials/{unknown}", 404, json={"password_hash": "x"}
            ),
            "user_not_found",
        )
        self.assertEqual(
            self.error_code(
                "GET", "/auth-store/credentials", 404, params={"email": "nobody@example.com"}
            ),
            "credential_not_found",
        )

    def test_a_session_is_hidden_once_revoked_replaced_or_expired(self):
        self.new_session("live")
        self.assertEqual(self.find("live").status_code, 200)
        self.new_session("expired", expires=PAST)
        self.assertEqual(self.find("expired").status_code, 404)
        self.call("POST", "/auth-store/sessions/revoke", 204, json={"refresh_hash": digest("live")})
        self.assertEqual(self.find("live").status_code, 404)

    def test_rotate_issues_a_new_session_and_retires_the_old(self):
        self.new_session("first")
        rotated = self.call(
            "POST",
            "/auth-store/sessions/rotate",
            200,
            json={
                "refresh_hash": digest("first"),
                "new_refresh_hash": digest("second"),
                "expires_at": FUTURE,
            },
        ).json()
        self.assertEqual(rotated["user_id"], self.user_id)
        self.assertEqual(self.find("second").status_code, 200)
        self.assertEqual(self.find("first").status_code, 404)

    def test_rotate_unknown_or_expired_token_is_refused(self):
        body = {
            "refresh_hash": digest("nope"),
            "new_refresh_hash": digest("n"),
            "expires_at": FUTURE,
        }
        self.assertEqual(
            self.error_code("POST", "/auth-store/sessions/rotate", 401, json=body),
            "refresh_invalid",
        )
        self.new_session("old", expires=PAST)
        body["refresh_hash"] = digest("old")
        self.assertEqual(
            self.error_code("POST", "/auth-store/sessions/rotate", 401, json=body),
            "refresh_expired",
        )

    def test_reusing_a_rotated_token_revokes_every_session_of_the_user(self):
        self.new_session("a")
        self.new_session("other-device")
        self.call(
            "POST",
            "/auth-store/sessions/rotate",
            200,
            json={
                "refresh_hash": digest("a"),
                "new_refresh_hash": digest("b"),
                "expires_at": FUTURE,
            },
        )
        reuse = {"refresh_hash": digest("a"), "new_refresh_hash": digest("c"), "expires_at": FUTURE}
        self.assertEqual(
            self.error_code("POST", "/auth-store/sessions/rotate", 409, json=reuse),
            "refresh_reused",
        )
        self.assertEqual(self.find("b").status_code, 404)
        self.assertEqual(self.find("other-device").status_code, 404)
        self.assertEqual(self.find("c").status_code, 404)

    def test_revoke_is_idempotent_and_revoke_all_clears_the_user(self):
        self.new_session("one")
        self.new_session("two")
        for _ in range(2):
            self.call(
                "POST", "/auth-store/sessions/revoke", 204, json={"refresh_hash": digest("one")}
            )
        self.assertEqual(self.find("two").status_code, 200)
        self.call("POST", f"/auth-store/users/{self.user_id}/sessions/revoke", 204)
        self.assertEqual(self.find("two").status_code, 404)

    def test_a_login_code_works_once_and_not_after_expiry(self):
        body = {"code_hash": digest("123456"), "email": self.email.upper(), "expires_at": FUTURE}
        self.call("POST", "/auth-store/login-codes", 201, json=body)
        consumed = self.call(
            "POST", "/auth-store/login-codes/consume", 200, json={"code_hash": digest("123456")}
        )
        self.assertEqual(consumed.json(), {"email": self.email})
        self.assertEqual(
            self.error_code(
                "POST", "/auth-store/login-codes/consume", 404, json={"code_hash": digest("123456")}
            ),
            "code_invalid",
        )
        expired = {"code_hash": digest("000000"), "email": self.email, "expires_at": PAST}
        self.call("POST", "/auth-store/login-codes", 201, json=expired)
        self.assertEqual(
            self.error_code(
                "POST", "/auth-store/login-codes/consume", 404, json={"code_hash": digest("000000")}
            ),
            "code_invalid",
        )

    def test_oauth_identity_upserts_and_reads_back(self):
        link = {"provider": "github", "subject": "12345", "user_id": self.user_id}
        self.call("PUT", "/auth-store/oauth-identities", 204, json=link)
        self.call("PUT", "/auth-store/oauth-identities", 204, json=link)
        found = self.call("GET", "/auth-store/oauth-identities/github/12345", 200)
        self.assertEqual(found.json(), {"user_id": self.user_id})
        self.assertEqual(
            self.error_code("GET", "/auth-store/oauth-identities/google/99", 404),
            "oauth_identity_not_found",
        )
        bad = {"provider": "gitlab", "subject": "1", "user_id": self.user_id}
        self.call("PUT", "/auth-store/oauth-identities", 422, json=bad)

    def test_a_bearer_token_is_refused_on_every_route(self):
        token, _ = signing.mint(self.team_a, tokens.AGENT_SCOPES, "agent", 600, None)
        headers = {"Authorization": f"Bearer {token}", "X-Data-API-Key": ""}
        requests = [
            ("PUT", f"/auth-store/credentials/{self.user_id}", {"json": {"password_hash": "x"}}),
            ("DELETE", f"/auth-store/credentials/{self.user_id}", {}),
            ("GET", "/auth-store/credentials", {"params": {"email": self.email}}),
            ("POST", "/auth-store/sessions", {"json": {}}),
            ("GET", "/auth-store/sessions", {"params": {"refresh_hash": "x"}}),
            ("POST", "/auth-store/sessions/rotate", {"json": {}}),
            ("POST", "/auth-store/sessions/revoke", {"json": {"refresh_hash": "x"}}),
            ("POST", f"/auth-store/users/{self.user_id}/sessions/revoke", {}),
            ("POST", "/auth-store/login-codes", {"json": {}}),
            ("POST", "/auth-store/login-codes/consume", {"json": {"code_hash": "x"}}),
            ("PUT", "/auth-store/oauth-identities", {"json": {}}),
            ("GET", "/auth-store/oauth-identities/github/1", {}),
        ]
        for method, path, kwargs in requests:
            with self.subTest(path=path):
                response = self.client.request(method, path, headers=headers, **kwargs)
                self.assertEqual(response.status_code, 401, response.text)
