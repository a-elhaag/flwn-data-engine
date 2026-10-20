"""What a token may see and attribute: authors, private channels, direct messages, private teams."""

from harness import CrudHarness

from app.api import tokens as t

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

    # -- authors ----------------------------------------------------------------

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

    # -- channels ---------------------------------------------------------------

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

    # -- teams ------------------------------------------------------------------

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
