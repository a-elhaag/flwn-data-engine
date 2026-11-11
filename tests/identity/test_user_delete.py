"""Deleting a user removes the account and every membership; the last owner is kept."""

import uuid

from harness import CrudHarness


class UserDeleteTests(CrudHarness):
    def human(self, workspace, email, role="member"):
        response = self.client.post(
            f"/workspaces/{workspace}/members",
            json={"type": "HUMAN", "name": "Ada", "email": email, "role": role},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_the_account_and_every_membership_are_removed(self):
        email = f"{uuid.uuid4().hex[:10]}@example.com"
        with_work = self.human(self.team_a, email)
        without_work = self.human(self.team_b, email)
        user = with_work["user_id"]

        team_a = self.client.post(
            f"/workspaces/{self.team_a}/teams", json={"key": "ENG", "name": "Eng"}
        ).json()
        self.client.put(
            f"/workspaces/{self.team_a}/teams/{team_a['id']}/members",
            json={"member_ids": [with_work["id"]]},
        )
        # Work: a memory the membership wrote.
        memory_id = self.remember(self.team_a)
        self.sql(
            "update memories set created_by = :m where id = :i", m=with_work["id"], i=memory_id
        )

        self.assertEqual(self.client.delete(f"/users/{user}").status_code, 204)

        self.assertEqual(
            self.sql(
                "select count(*) as n from members where id in (:a, :b)",
                a=with_work["id"],
                b=without_work["id"],
            )[0]["n"],
            0,
        )
        # Team seats go with the membership; the work keeps its row, with no author now.
        self.assertEqual(
            self.sql(
                "select count(*) as n from team_members where member_id = :m", m=with_work["id"]
            )[0]["n"],
            0,
        )
        self.assertIsNone(self.row(memory_id)["created_by"])
        self.assertEqual(self.client.get(f"/users/{user}").status_code, 404)
        # The audit log keeps who was removed, after the rows are gone.
        event = self.sql(
            "select changes from events where entity_type = 'member' and entity_id = :m and action = 'deleted'",
            m=with_work["id"],
        )[0]
        self.assertEqual(event["changes"]["user"], "Ada")

    def test_the_last_owner_cannot_be_deleted(self):
        owner = self.human(self.team_a, f"{uuid.uuid4().hex[:10]}@example.com", role="owner")
        response = self.client.delete(f"/users/{owner['user_id']}")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("last_owner", response.text)
        self.assertEqual(self.client.get(f"/users/{owner['user_id']}").status_code, 200)
