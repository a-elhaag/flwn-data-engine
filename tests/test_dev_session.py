import uuid
from unittest.mock import patch

from harness import CrudHarness

from app import dev_session
from app.api import tokens as t


class DevSessionTests(CrudHarness):
    def setUp(self):
        super().setUp()
        # the test client connects as "testclient", not from loopback
        for name in ("enabled", "loopback", "direct"):
            patcher = patch.object(dev_session, name, return_value=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.email = f"{uuid.uuid4().hex[:8]}@example.com"
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.sql("delete from workspaces where slug like 'personal-%' or slug = 'dev-shared'")

    def sign_in(self, email=None, name="Dev"):
        response = self.client.post(
            "/dev/session",
            json={"email": email or self.email, "name": name},
            headers={"X-Data-API-Key": ""},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_anyone_gets_a_personal_and_a_shared_workspace_without_credentials(self):
        got = self.sign_in()
        personal, shared = got["personal"], got["shared"]
        self.assertNotEqual(personal["workspace_id"], shared["workspace_id"])
        for entry in (personal, shared):
            claims = t.verify(entry["token"])
            self.assertEqual(
                (claims.workspace_id, claims.member_id, claims.subject),
                (entry["workspace_id"], entry["member_id"], got["user_id"]),
            )
            self.assertIn(t.SCOPE_ADMIN, claims.scopes)
            headers = {"Authorization": f"Bearer {entry['token']}", "X-Data-API-Key": ""}
            ok = self.client.get(f"/workspaces/{entry['workspace_id']}/projects", headers=headers)
            self.assertEqual(ok.status_code, 200, ok.text)
        # a token opens its own workspace only
        mine = {"Authorization": f"Bearer {personal['token']}", "X-Data-API-Key": ""}
        other = self.client.get(f"/workspaces/{shared['workspace_id']}/projects", headers=mine)
        self.assertEqual(other.status_code, 403)

    def test_signing_in_again_returns_the_same_workspaces_and_members(self):
        first = self.sign_in()
        second = self.sign_in(self.email.upper())
        for key in ("personal", "shared"):
            self.assertEqual(first[key]["workspace_id"], second[key]["workspace_id"])
            self.assertEqual(first[key]["member_id"], second[key]["member_id"])
        self.assertEqual(first["user_id"], second["user_id"])

    def test_people_share_one_workspace_but_not_the_personal_one(self):
        ada, bob = self.sign_in(), self.sign_in(f"{uuid.uuid4().hex[:8]}@example.com", "Bob")
        self.assertEqual(ada["shared"]["workspace_id"], bob["shared"]["workspace_id"])
        self.assertNotEqual(ada["shared"]["member_id"], bob["shared"]["member_id"])
        self.assertNotEqual(ada["personal"]["workspace_id"], bob["personal"]["workspace_id"])

    def test_it_is_off_unless_the_database_is_local(self):
        patch.stopall()  # the setUp patch of enabled() must not hide the real check
        for url, expected in (
            ("postgresql+psycopg://u:p@localhost:5432/db", True),
            ("postgresql+psycopg://u:p@127.0.0.1/db", True),
            ("postgresql+psycopg://u:p@/db?host=/tmp/pgsock", True),
            ("postgresql+psycopg://u:p@flwn.postgres.database.azure.com/db", False),
            ("postgresql+psycopg://u:p@10.0.0.5/db", False),
            ("postgresql+psycopg://u:p@localhost.evil.example.com/db", False),
            ("postgresql+psycopg://u:p@localhost/db?hostaddr=10.0.0.5", False),
            ("postgresql+psycopg://u:p@localhost,10.0.0.5/db", False),
            ("postgresql+psycopg://u:p@/db", True),
        ):
            with self.subTest(url=url), patch.object(dev_session, "database_url", return_value=url):
                self.assertEqual(dev_session.enabled(), expected)

    def test_route_answers_404_and_creates_nothing_on_a_remote_database(self):
        patch.stopall()
        remote = "postgresql+psycopg://u:p@flwn.postgres.database.azure.com/db"
        with patch.object(dev_session, "database_url", return_value=remote):
            response = self.client.post(
                "/dev/session", json={"email": self.email}, headers={"X-Data-API-Key": ""}
            )
        self.assertEqual(response.status_code, 404)
        found = self.sql("select count(*) as n from users where email = :e", e=self.email)
        self.assertEqual(found[0]["n"], 0)

    def test_route_is_not_in_the_api_docs(self):
        paths = self.client.get("/openapi.json").json()["paths"]
        self.assertNotIn("/dev/session", paths)

    def test_a_real_account_is_refused_and_not_touched(self):
        (real,) = self.sql(
            "insert into users (email, name) values (:e, 'Real') returning id", e=self.email
        )
        self.addCleanup(self.sql, "delete from members where user_id = :i", i=real["id"])
        self.addCleanup(self.sql, "delete from users where id = :i", i=real["id"])
        response = self.client.post(
            "/dev/session", json={"email": self.email.upper()}, headers={"X-Data-API-Key": ""}
        )
        self.assertEqual(response.status_code, 409, response.text)
        (n,) = self.sql("select count(*) as n from members where user_id = :i", i=real["id"])
        self.assertEqual(n["n"], 0)

    def test_only_loopback_callers_are_served(self):
        patch.stopall()  # the setUp patches would answer for the real checks
        self.assertTrue(all(dev_session.loopback(h) for h in ("127.0.0.1", "::1", "localhost")))
        self.assertFalse(any(dev_session.loopback(h) for h in ("10.0.0.5", "203.0.113.9", None)))
        with patch.object(dev_session, "enabled", return_value=True):
            response = self.client.post(  # the test client's address is not loopback
                "/dev/session", json={"email": self.email}, headers={"X-Data-API-Key": ""}
            )
        self.assertEqual(response.status_code, 404)

    def test_a_hostless_url_is_remote_when_the_environment_points_elsewhere(self):
        patch.stopall()
        url = "postgresql+psycopg://u:p@/db"
        with patch.object(dev_session, "database_url", return_value=url):
            for name in dev_session.PG_ENV:
                with self.subTest(env=name), patch.dict("os.environ", {name: "db.example.com"}):
                    self.assertFalse(dev_session.enabled())

    def test_a_real_workspace_with_the_shared_name_is_not_reused(self):
        self.sql("delete from workspaces where slug = 'dev-shared'")
        (real,) = self.sql(
            "insert into workspaces (name, slug) values ('Real', 'dev-shared') returning id"
        )
        self.addCleanup(self.sql, "delete from workspaces where id = :i", i=real["id"])
        response = self.client.post(
            "/dev/session", json={"email": self.email}, headers={"X-Data-API-Key": ""}
        )
        self.assertEqual(response.status_code, 409, response.text)
        (n,) = self.sql("select count(*) as n from members where workspace_id = :w", w=real["id"])
        self.assertEqual(n["n"], 0)

    def test_browser_and_proxy_requests_are_not_direct(self):
        patch.stopall()
        for headers, expected in (
            ({"host": "localhost:8002"}, True),
            ({"host": "127.0.0.1:8002"}, True),
            ({"host": "[::1]:8002"}, True),
            ({"host": "evil.example.com"}, False),  # DNS rebinding names its own site
            ({"host": "localhost.evil.example.com"}, False),
            ({"host": "localhost:8002", "origin": "https://evil.example.com"}, False),
            ({"host": "localhost:8002", "x-forwarded-for": "203.0.113.9"}, False),
            ({"host": "localhost:8002", "forwarded": "for=203.0.113.9"}, False),
            ({}, False),
        ):
            with self.subTest(headers=headers):
                self.assertEqual(dev_session.direct(headers), expected)
