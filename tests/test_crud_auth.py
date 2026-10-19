"""Who may call the CRUD routes: workspaces, members, teams, planning and collaboration."""

from harness import CrudHarness

from app.api import tokens as t

MEMBER = {"type": "HUMAN", "name": "Grace", "email": "grace@example.com", "role": "member"}


class CrudAuthTests(CrudHarness):
    def test_service_key_is_refused_on_crud_routes(self):
        service = {"X-Data-API-Key": "test-data-key", "Authorization": ""}
        for path in ("projects", "members", "teams", "docs", f"../{self.team_a}"):
            with self.subTest(path=path):
                response = self.client.get(f"/workspaces/{self.team_a}/{path}", headers=service)
                self.assertEqual(response.status_code, 401, response.text)

    def test_workspace_create_and_list_stay_backend_only(self):
        headers = self.token_headers(self.team_a, t.SCOPE_ADMIN)
        self.assertEqual(self.client.get("/workspaces", headers=headers).status_code, 401)
        created = self.client.post(
            "/workspaces", json={"name": "New", "slug": "new-one"}, headers=headers
        )
        self.assertEqual(created.status_code, 401)

    def test_token_is_locked_to_its_workspace(self):
        headers = self.token_headers(self.team_a, t.SCOPE_PLANNING_READ)
        self.assertEqual(
            self.client.get(f"/workspaces/{self.team_a}/projects", headers=headers).status_code, 200
        )
        other = self.client.get(f"/workspaces/{self.team_b}/projects", headers=headers)
        self.assertEqual(other.status_code, 403, other.text)

    def test_read_scope_cannot_write(self):
        read = self.token_headers(self.team_a, t.SCOPE_PLANNING_READ)
        url = f"/workspaces/{self.team_a}/projects"
        denied = self.client.post(url, json={"key": "ENG", "name": "E"}, headers=read)
        self.assertEqual(denied.status_code, 403, denied.text)
        write = self.token_headers(self.team_a, t.SCOPE_PLANNING_WRITE)
        self.assertEqual(
            self.client.post(url, json={"key": "ENG", "name": "E"}, headers=write).status_code, 201
        )

    def test_scopes_do_not_cross_areas(self):
        planning = self.token_headers(self.team_a, t.SCOPE_PLANNING_READ, t.SCOPE_PLANNING_WRITE)
        for path in ("members", "teams", "docs", "channels"):
            with self.subTest(path=path):
                response = self.client.get(f"/workspaces/{self.team_a}/{path}", headers=planning)
                self.assertEqual(response.status_code, 403, response.text)

    def test_members_and_workspace_changes_need_admin(self):
        writer = self.token_headers(self.team_a, t.SCOPE_IDENTITY_READ, t.SCOPE_IDENTITY_WRITE)
        base = f"/workspaces/{self.team_a}"
        self.assertEqual(
            self.client.post(f"{base}/members", json=MEMBER, headers=writer).status_code, 403
        )
        self.assertEqual(
            self.client.patch(base, json={"name": "X"}, headers=writer).status_code, 403
        )
        self.assertEqual(self.client.delete(base, headers=writer).status_code, 403)
        self.assertEqual(self.client.get(base, headers=writer).status_code, 200)
        self.assertEqual(
            self.client.post(
                f"{base}/teams", json={"key": "CORE", "name": "Core"}, headers=writer
            ).status_code,
            201,
        )
        admin = self.token_headers(self.team_a, t.SCOPE_ADMIN)
        self.assertEqual(
            self.client.post(f"{base}/members", json=MEMBER, headers=admin).status_code, 201
        )

    def test_suspended_member_loses_access_immediately(self):
        member = self.member(self.team_a)
        headers = self.token_headers(self.team_a, t.SCOPE_PLANNING_READ, member=member)
        url = f"/workspaces/{self.team_a}/projects"
        self.assertEqual(self.client.get(url, headers=headers).status_code, 200)
        self.sql("update members set status = 'suspended' where id = :i", i=member)
        denied = self.client.get(url, headers=headers)
        self.assertEqual(denied.status_code, 403, denied.text)

    def test_agent_tokens_cannot_carry_admin(self):
        with self.assertRaises(t.TokenError):
            t._atom_claims(
                self.member(self.team_a, "AI"),
                "00000000-0000-0000-0000-000000000001",
                "00000000-0000-0000-0000-000000000002",
                {t.SCOPE_ADMIN},
                300,
                "workspace",
            )
