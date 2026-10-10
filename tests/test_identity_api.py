from harness import MemoryHarness


class IdentityApiTests(MemoryHarness):
    def test_member_crud_links_human_profile_to_user(self):
        response = self.client.post(
            f"/workspaces/{self.team_a}/members",
            json={
                "type": "HUMAN",
                "name": "Ada Lovelace",
                "email": "ada@example.com",
                "role": "member",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        member = response.json()
        member_id = member["id"]
        self.assertEqual(
            (member["name"], member["email"], member["type"]),
            ("Ada Lovelace", "ada@example.com", "HUMAN"),
        )
        self.assertIsNotNone(member["user_id"])

        updated = self.client.patch(
            f"/workspaces/{self.team_a}/members/{member_id}",
            json={"name": "Ada Byron", "status": "suspended"},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(
            (updated.json()["name"], updated.json()["status"]), ("Ada Byron", "suspended")
        )
        self.assertEqual(
            self.client.get(f"/workspaces/{self.team_a}/members/{member_id}").status_code, 200
        )
        self.assertEqual(
            self.client.delete(f"/workspaces/{self.team_a}/members/{member_id}").status_code, 204
        )
        self.assertEqual(
            self.client.get(f"/workspaces/{self.team_a}/members/{member_id}").status_code, 404
        )

    def test_duplicate_member_and_foreign_team_member_are_rejected(self):
        member_body = {
            "type": "HUMAN",
            "name": "Ada Lovelace",
            "email": "ada@example.com",
        }
        first = self.client.post(f"/workspaces/{self.team_a}/members", json=member_body)
        self.assertEqual(first.status_code, 201, first.text)
        duplicate = self.client.post(f"/workspaces/{self.team_a}/members", json=member_body)
        self.assertEqual(duplicate.status_code, 409, duplicate.text)

        team = self.client.post(
            f"/workspaces/{self.team_a}/teams", json={"key": "ENG", "name": "Engineering"}
        )
        self.assertEqual(team.status_code, 201, team.text)
        foreign = self.member(self.team_b)
        response = self.client.put(
            f"/workspaces/{self.team_a}/teams/{team.json()['id']}/members",
            json={"member_ids": [foreign]},
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_team_crud_and_member_assignment(self):
        member_id = self.member(self.team_a)
        response = self.client.post(
            f"/workspaces/{self.team_a}/teams",
            json={"key": "ENG", "name": "Engineering", "visibility": "private"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        team_id = response.json()["id"]
        self.assertEqual(response.json()["visibility"], "private")

        assigned = self.client.put(
            f"/workspaces/{self.team_a}/teams/{team_id}/members",
            json={"member_ids": [member_id]},
        )
        self.assertEqual(assigned.status_code, 200, assigned.text)
        self.assertEqual(
            self.sql(
                "select count(*) as n from team_members where workspace_id = :w and team_id = :t",
                w=self.team_a,
                t=team_id,
            )[0]["n"],
            1,
        )
        updated = self.client.patch(
            f"/workspaces/{self.team_a}/teams/{team_id}", json={"name": "Platform"}
        )
        self.assertEqual(updated.json()["name"], "Platform")
        self.assertEqual(
            self.client.delete(f"/workspaces/{self.team_a}/teams/{team_id}").status_code, 204
        )
        self.assertEqual(
            self.client.get(f"/workspaces/{self.team_a}/teams/{team_id}").status_code, 404
        )

    def test_member_routes_require_service_credentials(self):
        response = self.client.get(
            f"/workspaces/{self.team_a}/members", headers={"X-Data-API-Key": ""}
        )
        self.assertEqual(response.status_code, 401)
