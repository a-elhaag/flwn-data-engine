"""Deleting a user: memberships with work are suspended, the rest removed; the last owner is kept."""

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

    def test_memberships_with_work_are_suspended_and_the_rest_removed(self):
        email = f"{uuid.uuid4().hex[:10]}@example.com"
        with_work = self.human(self.team_a, email)
        without_work = self.human(self.team_b, email)
        user = with_work["user_id"]

        team_a = self.client.post(
            f"/workspaces/{self.team_a}/teams", json={"key": "ENG", "name": "Eng"}
        ).json()
        team_b = self.client.post(
            f"/workspaces/{self.team_b}/teams", json={"key": "OPS", "name": "Ops"}
        ).json()
        self.client.put(
            f"/workspaces/{self.team_a}/teams/{team_a['id']}/members",
            json={"member_ids": [with_work["id"]]},
        )
        self.client.put(
            f"/workspaces/{self.team_b}/teams/{team_b['id']}/members",
            json={"member_ids": [without_work["id"]]},
        )
        # Work: a memory the first membership wrote.
        memory_id = self.remember(self.team_a)
        self.sql(
            "update memories set created_by = :m where id = :i", m=with_work["id"], i=memory_id
        )

        self.assertEqual(self.client.delete(f"/users/{user}").status_code, 204)

        with_work_row = self.sql(
            "select status, deleted_at from members where id = :m", m=with_work["id"]
        )[0]
        self.assertEqual(
            (with_work_row["status"], with_work_row["deleted_at"]), ("suspended", None)
        )
        without_work_row = self.sql(
            "select status, deleted_at from members where id = :m", m=without_work["id"]
        )[0]
        self.assertIsNotNone(without_work_row["deleted_at"])
        # A suspended member keeps their team seats; a removed one loses them.
        seats = self.sql(
            "select member_id from team_members where member_id in (:a, :b)",
            a=with_work["id"],
            b=without_work["id"],
        )
        self.assertEqual([str(s["member_id"]) for s in seats], [with_work["id"]])
        # The past work still names who did it.
        self.assertEqual(str(self.row(memory_id)["created_by"]), with_work["id"])
        self.assertEqual(self.client.get(f"/users/{user}").status_code, 404)

    def test_the_last_owner_cannot_be_deleted(self):
        owner = self.human(self.team_a, f"{uuid.uuid4().hex[:10]}@example.com", role="owner")
        response = self.client.delete(f"/users/{owner['user_id']}")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("last_owner", response.text)
        self.assertEqual(self.client.get(f"/users/{owner['user_id']}").status_code, 200)
