"""A row of one workspace must look missing to every other workspace, and a token must not cross.

Ported in spirit from the feature branch's cross-cutting tests (organization -> workspace)."""

import uuid
from unittest.mock import patch

from harness import CrudHarness
from sqlalchemy import create_engine, event, text

from app.api import tokens as t
from app.db.install import grant_app_role
from app.db.session import bind_viewer, workspace_session


class TenantIsolationTests(CrudHarness):
    def post(self, path, body):
        response = self.client.post(f"/workspaces/{self.team_a}{path}", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def make(self):
        """(name, path inside the workspace, a PATCH body) for one row of every kind."""
        project = self.post("/projects", {"key": "ISO", "name": "Iso"})
        item = self.post(f"/projects/{project}/work-items", {"title": "w", "type": "FEATURE"})
        base = f"/projects/{project}"
        return [
            (
                "member",
                "/members/"
                + self.post(
                    "/members",
                    {
                        "type": "HUMAN",
                        "name": "Ada",
                        "email": f"{uuid.uuid4().hex[:8]}@example.com",
                    },
                ),
                {"name": "Renamed"},
            ),
            ("team", "/teams/" + self.post("/teams", {"key": "ISO", "name": "T"}), {"name": "R"}),
            ("project", base, {"name": "Renamed"}),
            ("work item", f"{base}/work-items/{item}", {"title": "Renamed"}),
            (
                "sprint",
                f"{base}/sprints/" + self.post(f"{base}/sprints", {"name": "S"}),
                {"name": "R"},
            ),
            (
                "task",
                f"{base}/tasks/" + self.post(f"{base}/tasks", {"work_item_id": item, "title": "t"}),
                {"title": "Renamed"},
            ),
            ("doc", "/docs/" + self.post("/docs", {"title": "D"}), {"title": "Renamed"}),
            (
                "channel",
                "/channels/" + self.post("/channels", {"type": "group", "name": "c"}),
                {"name": "Renamed"},
            ),
        ]

    def test_other_workspace_sees_every_row_as_missing(self):
        mine = self.token_headers(self.team_a, t.SCOPE_ADMIN, member=self.admin_member(self.team_a))
        theirs = self.token_headers(
            self.team_b, t.SCOPE_ADMIN, member=self.admin_member(self.team_b)
        )
        for name, path, change in self.make():
            with self.subTest(row=name):
                own, other = f"/workspaces/{self.team_a}{path}", f"/workspaces/{self.team_b}{path}"
                self.assertEqual(self.client.get(own, headers=mine).status_code, 200, own)
                # B's own workspace has no such row ...
                self.assertEqual(self.client.get(other, headers=theirs).status_code, 404, other)
                self.assertEqual(
                    self.client.patch(other, json=change, headers=theirs).status_code, 404
                )
                self.assertEqual(self.client.delete(other, headers=theirs).status_code, 404)
                # ... and B's token cannot name A's workspace in the URL.
                for call in (self.client.get, self.client.delete):
                    self.assertEqual(call(own, headers=theirs).status_code, 403, own)
                self.assertEqual(
                    self.client.patch(own, json=change, headers=theirs).status_code, 403
                )
                self.assertEqual(self.client.get(own, headers=mine).status_code, 200, "untouched")

    def test_service_key_and_wrong_secret_tokens_never_reach_a_workspace(self):
        path = f"/workspaces/{self.team_a}/projects"
        service = {"X-Data-API-Key": "test-data-key", "Authorization": ""}
        self.assertEqual(self.client.get(path, headers=service).status_code, 200)
        forged = {"Authorization": "Bearer not.a.token", "X-Data-API-Key": ""}
        self.assertEqual(self.client.get(path, headers=forged).status_code, 401)


COLLAB = (t.SCOPE_COLLAB_READ, t.SCOPE_COLLAB_WRITE)
IDENTITY = (t.SCOPE_IDENTITY_READ, t.SCOPE_IDENTITY_WRITE)


class VisibilityTests(CrudHarness):
    def setUp(self):
        super().setUp()
        self.ws = self.team_a
        self.ada, self.bob, self.eve = (self.member(self.ws) for _ in range(3))
        self.root = f"/workspaces/{self.ws}"

    def as_(self, member, *scopes):
        return self.token_headers(self.ws, *(scopes or COLLAB), member=member)

    def admin(self):
        return self.token_headers(self.ws, t.SCOPE_ADMIN, member=self.admin_member(self.ws))

    def channel(self, creator, kind="group", **body):
        response = self.client.post(
            f"{self.root}/channels",
            json={"type": kind, "name": "c", **body},
            headers=self.as_(creator),
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def ids(self, path, headers):
        return [row["id"] for row in self.client.get(path, headers=headers).json()["items"]]

    def test_message_and_comment_author_is_always_the_token_member(self):
        channel = self.channel(self.ada)
        url = f"{self.root}/channels/{channel}/messages"
        sent = self.client.post(url, json={"body": "hi"}, headers=self.as_(self.ada))
        self.assertEqual(sent.json()["author_id"], self.ada)
        forged = self.client.post(
            url, json={"body": "hi", "author_id": self.bob}, headers=self.as_(self.ada)
        )
        self.assertEqual(forged.status_code, 403, forged.text)
        nobody = self.token_headers(self.ws, *COLLAB)  # a token bound to no member
        self.assertEqual(self.client.post(url, json={"body": "x"}, headers=nobody).status_code, 403)

        project = self.client.post(f"{self.root}/projects", json={"key": "ENG", "name": "E"}).json()
        comment = {"target_type": "project", "target_id": project["id"], "body": "note"}
        ok = self.client.post(f"{self.root}/comments", json=comment, headers=self.as_(self.ada))
        self.assertEqual(ok.json()["author_id"], self.ada)
        fake = {**comment, "author_id": self.bob}
        self.assertEqual(
            self.client.post(
                f"{self.root}/comments", json=fake, headers=self.as_(self.ada)
            ).status_code,
            403,
        )

    def test_only_the_author_or_an_admin_changes_a_message(self):
        channel = self.channel(self.ada, member_ids=[self.bob])
        url = f"{self.root}/channels/{channel}/messages"
        message = self.client.post(url, json={"body": "mine"}, headers=self.as_(self.ada)).json()
        one = f"{url}/{message['id']}"
        edit = {"body": "changed"}
        self.assertEqual(
            self.client.patch(one, json=edit, headers=self.as_(self.bob)).status_code, 403
        )
        self.assertEqual(self.client.delete(one, headers=self.as_(self.bob)).status_code, 403)
        self.assertEqual(
            self.client.patch(one, json=edit, headers=self.as_(self.ada)).status_code, 200
        )
        self.assertEqual(self.client.delete(one, headers=self.admin()).status_code, 204)

    def test_private_channel_is_hidden_from_non_members_but_not_from_admin(self):
        private = self.channel(self.ada, is_private=True, member_ids=[self.bob])
        open_ = self.channel(self.ada)
        listing = f"{self.root}/channels"
        self.assertEqual(set(self.ids(listing, self.as_(self.bob))), {private, open_})
        self.assertEqual(self.ids(listing, self.as_(self.eve)), [open_])
        self.assertEqual(set(self.ids(listing, self.admin())), {private, open_})
        for path in (f"{listing}/{private}", f"{listing}/{private}/messages"):
            self.assertEqual(self.client.get(path, headers=self.as_(self.eve)).status_code, 404)
        posting = self.client.post(
            f"{listing}/{private}/messages", json={"body": "x"}, headers=self.as_(self.eve)
        )
        self.assertEqual(posting.status_code, 404)

    def test_direct_and_agent_channels_are_members_only_even_for_admin(self):
        for kind in ("dm", "agent"):
            with self.subTest(kind=kind):
                channel = self.channel(self.ada, kind, member_ids=[self.bob])
                listing = f"{self.root}/channels"
                self.assertIn(channel, self.ids(listing, self.as_(self.bob)))
                self.assertNotIn(channel, self.ids(listing, self.as_(self.eve)))
                self.assertNotIn(channel, self.ids(listing, self.admin()))
                self.assertEqual(
                    self.client.get(f"{listing}/{channel}", headers=self.admin()).status_code, 404
                )

    def test_only_channel_owners_and_admins_change_membership(self):
        channel = self.channel(self.ada, member_ids=[self.bob])
        url = f"{self.root}/channels/{channel}/members"
        body = {"member_ids": [self.ada, self.bob, self.eve]}
        self.assertEqual(
            self.client.put(url, json=body, headers=self.as_(self.bob)).status_code, 403
        )
        privacy = self.client.patch(
            f"{self.root}/channels/{channel}", json={"is_private": True}, headers=self.as_(self.bob)
        )
        self.assertEqual(privacy.status_code, 403, privacy.text)
        self.assertEqual(
            self.client.put(url, json=body, headers=self.as_(self.ada)).status_code, 200
        )
        self.assertEqual(self.client.put(url, json=body, headers=self.admin()).status_code, 200)

    def test_private_team_is_visible_to_its_members_and_admins_only(self):
        writer = self.as_(self.eve, *IDENTITY)
        team = self.client.post(
            f"{self.root}/teams",
            json={"key": "SEC", "name": "Security", "visibility": "private"},
            headers=writer,
        ).json()["id"]
        self.assertEqual(
            self.client.put(
                f"{self.root}/teams/{team}/members",
                json={"member_ids": [self.ada]},
                headers=self.admin(),
            ).status_code,
            200,
        )
        listing = f"{self.root}/teams"
        self.assertIn(team, self.ids(listing, self.as_(self.ada, *IDENTITY)))
        self.assertNotIn(team, self.ids(listing, writer))
        self.assertEqual(self.client.get(f"{listing}/{team}", headers=writer).status_code, 404)
        self.assertIn(team, self.ids(listing, self.admin()))

    def test_team_members_and_visibility_need_admin(self):
        writer = self.as_(self.eve, *IDENTITY)
        team = self.client.post(
            f"{self.root}/teams", json={"key": "OPS", "name": "Ops"}, headers=writer
        ).json()["id"]
        one = f"{self.root}/teams/{team}"
        join = self.client.put(f"{one}/members", json={"member_ids": [self.eve]}, headers=writer)
        self.assertEqual(join.status_code, 403, join.text)
        flip = self.client.patch(one, json={"visibility": "private"}, headers=writer)
        self.assertEqual(flip.status_code, 403, flip.text)
        rename = self.client.patch(one, json={"name": "Operations"}, headers=writer)
        self.assertEqual(rename.status_code, 200, rename.text)
        self.assertEqual(
            self.client.patch(
                one, json={"visibility": "private"}, headers=self.admin()
            ).status_code,
            200,
        )

    def test_deleting_a_team_needs_admin(self):
        writer = self.as_(self.eve, *IDENTITY)
        team = self.client.post(
            f"{self.root}/teams", json={"key": "TMP", "name": "Temp"}, headers=writer
        ).json()["id"]
        one = f"{self.root}/teams/{team}"
        self.assertEqual(self.client.delete(one, headers=writer).status_code, 403)
        self.assertEqual(self.client.delete(one, headers=self.admin()).status_code, 204)


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
