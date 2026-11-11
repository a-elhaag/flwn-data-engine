"""Invitations: the only way a human becomes a member, accepted with the backend's service key."""

import uuid

from harness import CrudHarness

from app.api import tokens as t


class InvitationsApiTests(CrudHarness):
    def email(self):
        return f"{uuid.uuid4().hex[:10]}@example.com"

    def invite(self, workspace, email=None, role="member"):
        response = self.client.post(
            f"/workspaces/{workspace}/invitations",
            json={"email": email or self.email(), "role": role},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def accept(self, workspace, token, name=None):
        body = {"token": token, **({"name": name} if name else {})}
        # No Authorization: the trusted backend's service key alone, as the backend calls it.
        return self.client.post(
            f"/workspaces/{workspace}/invitations/accept", json=body, headers={"Authorization": ""}
        )

    def test_invite_then_accept_makes_an_active_member(self):
        email = self.email()
        invitation = self.invite(self.team_a, email, role="admin")
        self.assertEqual((invitation["status"], invitation["email"]), ("pending", email))

        accepted = self.accept(self.team_a, invitation["token"], name="Sara Ali")
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(
            (accepted.json()["email"], accepted.json()["role"], accepted.json()["status"]),
            (email, "admin", "active"),
        )
        listed = self.client.get(f"/workspaces/{self.team_a}/invitations").json()["items"]
        self.assertEqual([i["status"] for i in listed], ["accepted"])
        self.assertNotIn("token", listed[0])

        again = self.accept(self.team_a, invitation["token"], name="Sara Ali")
        self.assertEqual(again.status_code, 409, again.text)

    def test_only_the_hash_of_the_token_is_stored(self):
        invitation = self.invite(self.team_a)
        stored = self.sql("select token_hash from invitations where id = :i", i=invitation["id"])[
            0
        ]["token_hash"]
        self.assertNotEqual(stored, invitation["token"])
        self.assertEqual(len(stored), 64)

    def test_an_expired_invitation_cannot_be_accepted(self):
        invitation = self.invite(self.team_a)
        self.sql(
            "update invitations set expires_at = now() - interval '1 minute' where id = :i",
            i=invitation["id"],
        )
        listed = self.client.get(f"/workspaces/{self.team_a}/invitations").json()["items"]
        self.assertEqual(listed[0]["status"], "expired")
        response = self.accept(self.team_a, invitation["token"], name="Late")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("expired", response.text)

    def test_a_revoked_invitation_cannot_be_accepted_or_revoked_again(self):
        invitation = self.invite(self.team_a)
        base = f"/workspaces/{self.team_a}/invitations/{invitation['id']}/revoke"
        revoked = self.client.post(base)
        self.assertEqual((revoked.status_code, revoked.json()["status"]), (200, "revoked"))
        self.assertEqual(self.client.post(base).status_code, 409)
        self.assertEqual(self.accept(self.team_a, invitation["token"], name="Ada").status_code, 409)

    def test_inviting_an_active_member_is_refused_and_a_reinvite_replaces_the_pending_one(self):
        email = self.email()
        self.client.post(
            f"/workspaces/{self.team_a}/members",
            json={"type": "HUMAN", "name": "Ada", "email": email},
        )
        refused = self.client.post(f"/workspaces/{self.team_a}/invitations", json={"email": email})
        self.assertEqual(refused.status_code, 409, refused.text)

        first = self.invite(self.team_a)
        second = self.invite(self.team_a, first["email"], role="viewer")
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(second["role"], "viewer")
        old = self.accept(self.team_a, first["token"], name="Ada")
        self.assertEqual(old.status_code, 404, "the old token stops working at once")

    def test_accepting_reactivates_a_suspended_member_with_the_invited_role(self):
        email = self.email()
        member = self.client.post(
            f"/workspaces/{self.team_a}/members",
            json={"type": "HUMAN", "name": "Ada", "email": email},
        ).json()
        self.client.patch(
            f"/workspaces/{self.team_a}/members/{member['id']}", json={"status": "suspended"}
        )
        invitation = self.invite(self.team_a, email, role="viewer")
        accepted = self.accept(self.team_a, invitation["token"])
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(accepted.json()["id"], member["id"])
        self.assertEqual((accepted.json()["status"], accepted.json()["role"]), ("active", "viewer"))

    def test_an_email_of_a_deleted_account_can_be_invited_again(self):
        email = self.email()
        user = self.client.post("/users", json={"email": email, "name": "Gone"}).json()
        self.assertEqual(self.client.delete(f"/users/{user['id']}").status_code, 204)
        invitation = self.invite(self.team_a, email)
        response = self.accept(self.team_a, invitation["token"], name="Back")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotEqual(response.json()["user_id"], user["id"])

    def test_invitations_are_not_a_generic_table(self):
        from app.api import generic_crud

        self.assertNotIn("invitations", generic_crud.CRUD_TABLES | generic_crud.READ_TABLES)
        self.assertNotIn("invitations", generic_crud.ACCESS)
        generic_paths = {getattr(r, "path", None) for r in generic_crud.router.routes}
        self.assertNotIn("/workspaces/{workspace_id}/invitations", generic_paths)
        # The invitation routes still answer on that path: they list and create.
        invitation = self.invite(self.team_a)
        listed = self.client.get(f"/workspaces/{self.team_a}/invitations")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertIn(invitation["id"], {i["id"] for i in listed.json()["items"]})
        self.assertNotIn("token_hash", listed.json()["items"][0])

    def test_accept_is_service_only_and_listing_needs_an_administrator(self):
        invitation = self.invite(self.team_a)
        headers = self.token_headers(self.team_a, t.SCOPE_ADMIN)
        response = self.client.post(
            f"/workspaces/{self.team_a}/invitations/accept",
            json={"token": invitation["token"], "name": "Ada"},
            headers=headers,
        )
        self.assertEqual(response.status_code, 403, response.text)

        reader = self.token_headers(self.team_a, t.SCOPE_IDENTITY_READ)
        self.assertEqual(
            self.client.get(f"/workspaces/{self.team_a}/invitations", headers=reader).status_code,
            403,
        )
