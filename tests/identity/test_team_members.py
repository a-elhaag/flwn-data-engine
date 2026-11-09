"""Team members: one at a time, PUT keeps roles and join dates, suspended members stay, team deletes."""

from harness import CrudHarness

from app.api import tokens as t


class TeamMembersApiTests(CrudHarness):
    def team(self, workspace, key="ENG"):
        response = self.client.post(
            f"/workspaces/{workspace}/teams", json={"key": key, "name": "Engineering"}
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def members_of(self, workspace, team_id):
        response = self.client.get(f"/workspaces/{workspace}/teams/{team_id}/members")
        self.assertEqual(response.status_code, 200, response.text)
        return {item["member_id"]: item for item in response.json()["items"]}

    def test_add_change_and_remove_one_member_at_a_time(self):
        base = f"/workspaces/{self.team_a}"
        team = self.team(self.team_a)
        ada, bob = self.member(self.team_a), self.member(self.team_a)

        added = self.client.post(
            f"{base}/teams/{team}/members", json={"member_id": ada, "role": "lead"}
        )
        self.assertEqual(added.status_code, 201, added.text)
        self.assertEqual((added.json()["role"], added.json()["status"]), ("lead", "active"))
        self.client.post(f"{base}/teams/{team}/members", json={"member_id": bob})
        self.assertEqual(
            {m: v["role"] for m, v in self.members_of(self.team_a, team).items()},
            {ada: "lead", bob: "member"},
        )

        changed = self.client.patch(f"{base}/teams/{team}/members/{bob}", json={"role": "lead"})
        self.assertEqual((changed.status_code, changed.json()["role"]), (200, "lead"))

        self.assertEqual(self.client.delete(f"{base}/teams/{team}/members/{ada}").status_code, 204)
        self.assertEqual(list(self.members_of(self.team_a, team)), [bob])

        # The team role is recorded, so "X made Y a lead" can be traced.
        events = [e for e in self.events(self.team_a, "team") if e["action"].startswith("member_")]
        self.assertEqual(
            [e["action"] for e in events],
            ["member_added", "member_added", "member_role_changed", "member_removed"],
        )
        self.assertEqual(events[0]["changes"]["role"], "lead")
        self.assertEqual(events[2]["changes"]["role"], "lead")

    def test_add_refuses_suspended_members_and_duplicates(self):
        base = f"/workspaces/{self.team_a}/teams"
        team = self.team(self.team_a)
        suspended = self.member(self.team_a, status="suspended")
        active = self.member(self.team_a)
        self.assertEqual(
            self.client.post(f"{base}/{team}/members", json={"member_id": suspended}).status_code,
            422,
        )
        self.assertEqual(
            self.client.post(f"{base}/{team}/members", json={"member_id": active}).status_code,
            201,
        )
        self.assertEqual(
            self.client.post(f"{base}/{team}/members", json={"member_id": active}).status_code,
            409,
        )

    def test_put_keeps_roles_join_dates_and_suspended_members(self):
        base = f"/workspaces/{self.team_a}/teams"
        team = self.team(self.team_a)
        lead, member, suspended = (
            self.member(self.team_a),
            self.member(self.team_a),
            self.member(self.team_a),
        )
        self.client.post(f"{base}/{team}/members", json={"member_id": lead, "role": "lead"})
        self.assertEqual(
            self.client.put(
                f"{base}/{team}/members", json={"member_ids": [lead, member, suspended]}
            ).status_code,
            200,
        )
        before = self.members_of(self.team_a, team)
        self.sql("update members set status = 'suspended' where id = :i", i=suspended)

        # The suspended member is already on the team, so keeping them is allowed.
        kept = self.client.put(
            f"{base}/{team}/members", json={"member_ids": [lead, member, suspended]}
        )
        self.assertEqual(kept.status_code, 200, kept.text)
        after = self.members_of(self.team_a, team)
        self.assertEqual(after[lead]["role"], "lead")
        self.assertEqual(after[lead]["joined_at"], before[lead]["joined_at"])
        self.assertEqual(after[suspended]["status"], "suspended")

        # Leaving someone out removes them; the others keep their place.
        self.client.put(f"{base}/{team}/members", json={"member_ids": [lead]})
        self.assertEqual(list(self.members_of(self.team_a, team)), [lead])

    def test_member_teams_lists_only_the_teams_they_are_on(self):
        mine, other = self.team(self.team_a, "ONE"), self.team(self.team_a, "TWO")
        self.team(self.team_a, "NONE")
        ana = self.member(self.team_a)
        self.client.put(
            f"/workspaces/{self.team_a}/teams/{mine}/members", json={"member_ids": [ana]}
        )
        self.client.post(
            f"/workspaces/{self.team_a}/teams/{other}/members", json={"member_id": ana}
        )
        teams = self.client.get(f"/workspaces/{self.team_a}/members/{ana}/teams").json()["items"]
        self.assertEqual({team["id"] for team in teams}, {mine, other})

    def test_a_team_with_projects_cannot_be_deleted_until_they_move(self):
        base = f"/workspaces/{self.team_a}"
        team = self.team(self.team_a)
        project = self.client.post(
            f"{base}/projects", json={"key": "DATA", "name": "Data", "team_id": team}
        )
        self.assertEqual(project.status_code, 201, project.text)
        blocked = self.client.delete(f"{base}/teams/{team}")
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(self.client.get(f"{base}/teams/{team}").status_code, 200)

        self.client.delete(f"{base}/projects/{project.json()['id']}")
        self.assertEqual(self.client.delete(f"{base}/teams/{team}").status_code, 204)

    def test_changing_members_needs_an_administrator(self):
        base = f"/workspaces/{self.team_a}/teams"
        team = self.team(self.team_a)
        ana = self.member(self.team_a)
        writer = self.token_headers(self.team_a, t.SCOPE_IDENTITY_READ, t.SCOPE_IDENTITY_WRITE)
        response = self.client.post(
            f"{base}/{team}/members", json={"member_id": ana}, headers=writer
        )
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(self.client.get(f"{base}/{team}/members", headers=writer).status_code, 200)
