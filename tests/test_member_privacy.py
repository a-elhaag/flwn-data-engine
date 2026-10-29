"""Inside one workspace, a request limited to a member sees only that member's private rows."""

import uuid
from unittest.mock import patch

from harness import CrudHarness
from sqlalchemy import create_engine, event, text

from app.db.install import grant_app_role
from app.db.session import bind_viewer, workspace_session

ROLE = "flwn_privacy_test"


class MemberPrivacyTests(CrudHarness):
    def setUp(self):
        super().setUp()
        # The test engine is a superuser, which bypasses row-level security: read as a plain role.
        with self.engine.begin() as conn:
            grant_app_role(conn, ROLE)
        self.limited = create_engine(self.engine.url, pool_size=2)
        event.listen(self.limited, "connect", lambda c, _: c.cursor().execute(f"set role {ROLE}"))
        self.addCleanup(self.limited.dispose)
        self.ws = self.workspace("priv")
        self.ada, self.bob = self.member(self.ws), self.member(self.ws)
        for who in (self.ada, self.bob):
            self.sql(
                "insert into notifications (workspace_id, recipient_id, type, title)"
                " values (:w, :m, 'mentioned', :t)",
                w=self.ws,
                m=who,
                t=f"for {who}",
            )
            self.sql(
                "insert into favorites (workspace_id, member_id, entity_type, entity_id)"
                " values (:w, :m, 'task', :e)",
                w=self.ws,
                m=who,
                e=str(uuid.uuid4()),
            )
        for owner, scope in ((None, "workspace"), (self.ada, "agent"), (self.bob, "agent")):
            self.sql(
                "insert into memories (workspace_id, kind, scope, owner_member_id, text)"
                " values (:w, 'fact', :s, :o, 'm')",
                w=self.ws,
                s=scope,
                o=owner,
            )

    def seen(self, viewer, table, column):
        """The values of `column` this viewer reads from `table` in the workspace."""
        with bind_viewer(viewer), workspace_session(self.limited, self.ws) as session:
            rows = session.execute(text(f"select {column} from {table}")).all()
        return {str(r[0]) if r[0] else None for r in rows}

    def test_member_sees_only_own_private_rows(self):
        for table, column in (("notifications", "recipient_id"), ("favorites", "member_id")):
            with self.subTest(table=table):
                self.assertEqual(self.seen(self.ada, table, column), {self.ada})

    def test_agent_memory_is_owned_but_workspace_memory_is_shared(self):
        self.assertEqual(self.seen(self.ada, "memories", "owner_member_id"), {None, self.ada})
        self.assertEqual(self.seen(self.bob, "memories", "owner_member_id"), {None, self.bob})

    def test_unnamed_caller_sees_no_private_rows(self):
        self.assertEqual(self.seen("", "notifications", "recipient_id"), set())
        self.assertEqual(self.seen("", "memories", "owner_member_id"), {None})

    def test_no_limit_sees_every_row(self):
        everyone = {self.ada, self.bob}
        self.assertEqual(self.seen(None, "notifications", "recipient_id"), everyone)
        self.assertEqual(self.seen(None, "memories", "owner_member_id"), {None, *everyone})

    def test_limit_cannot_cross_workspaces(self):
        other = self.workspace("other")
        with bind_viewer(self.ada), workspace_session(self.limited, other) as session:
            self.assertEqual(session.execute(text("select 1 from notifications")).all(), [])

    def test_service_key_acting_as_member_is_limited_to_that_member(self):
        url = f"/workspaces/{self.ws}/notifications"
        service = {"Authorization": "", "X-Data-API-Key": "test-data-key"}
        with patch("app.db.session.engine", return_value=self.limited):
            everyone = self.client.get(url, headers=service)
            self.assertEqual(everyone.status_code, 200, everyone.text)
            self.assertEqual(
                {r["recipient_id"] for r in everyone.json()["items"]}, {self.ada, self.bob}
            )
            mine = self.client.get(url, headers={**service, "X-Acting-Member-Id": self.ada})
            self.assertEqual({r["recipient_id"] for r in mine.json()["items"]}, {self.ada})
