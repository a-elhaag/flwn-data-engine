"""Who may call the CRUD routes: workspaces, members, teams, planning and collaboration."""

from harness import CrudHarness

from app.api import tokens as t

MEMBER = {"type": "HUMAN", "name": "Grace", "email": "grace@example.com", "role": "member"}


class CrudAuthTests(CrudHarness):
    def test_service_key_is_accepted_on_crud_routes(self):
        service = {"X-Data-API-Key": "test-data-key", "Authorization": ""}
        for path in ("projects", "members", "teams", "docs"):
            with self.subTest(path=path):
                response = self.client.get(f"/workspaces/{self.team_a}/{path}", headers=service)
                self.assertEqual(response.status_code, 200, response.text)

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

    def user_of(self, member):
        return str(self.sql("select user_id from members where id = :i", i=member)[0]["user_id"])

    def mint(self, member, **extra):
        body = {"workspace_id": self.team_a, "member_id": member, "scopes": ["admin"], **extra}
        return self.client.post("/auth/tokens", json=body)

    def test_human_token_subject_is_the_users_id(self):
        human = self.member(self.team_a)
        default = self.mint(human)
        self.assertEqual(default.status_code, 200, default.text)
        claims = t.verify(default.json()["token"])
        self.assertEqual(claims.subject, self.user_of(human))
        self.assertEqual(self.mint(human, subject=self.user_of(human)).status_code, 200)
        self.assertEqual(self.mint(human, subject="someone-else").status_code, 422)

    def test_agent_members_keep_a_free_subject(self):
        agent = self.member(self.team_a, "AI")
        ok = self.mint(agent, subject="planner")
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(t.verify(ok.json()["token"]).subject, "planner")

    def test_changes_are_attributed_to_the_token_member(self):
        who = self.member(self.team_a)
        headers = self.token_headers(self.team_a, t.SCOPE_ADMIN, member=who)
        base = f"/workspaces/{self.team_a}"
        project = self.client.post(
            f"{base}/projects", json={"key": "ATT", "name": "A"}, headers=headers
        ).json()
        item = self.client.post(
            f"{base}/projects/{project['id']}/work-items",
            json={"title": "w", "type": "FEATURE"},
            headers=headers,
        ).json()
        task = self.client.post(
            f"{base}/projects/{project['id']}/tasks",
            json={"work_item_id": item["id"], "title": "t"},
            headers=headers,
        ).json()
        self.assertEqual(task["created_by"], who)
        for table, row in (("projects", project), ("work_items", item)):
            (found,) = self.sql(f"select created_by from {table} where id = :i", i=row["id"])
            self.assertEqual(str(found["created_by"]), who, table)
        self.client.patch(f"{base}/projects/{project['id']}", json={"name": "B"}, headers=headers)
        self.client.patch(base, json={"description": "d"}, headers=headers)
        actors = {str(e["actor_id"]) for e in self.events(self.team_a) if e["actor_id"]}
        self.assertEqual(actors, {who})
        kinds = {e["entity_type"] for e in self.events(self.team_a) if e["actor_id"]}
        self.assertTrue({"project", "work_item", "task", "workspace"} <= kinds)


class GenericTableAuthTests(CrudHarness):
    def test_access_map_names_real_tables(self):
        from app.api import generic_crud

        self.assertLessEqual(
            set(generic_crud.ACCESS), generic_crud.CRUD_TABLES | generic_crud.READ_TABLES
        )

    def test_read_only_scope_tables_have_no_write_routes(self):
        from app.api import generic_crud
        from app.main import app

        paths = app.openapi()["paths"]
        for table, (_, write) in generic_crud.ACCESS.items():
            if write is None:
                path = f"/workspaces/{{workspace_id}}/{table.replace('_', '-')}"
                with self.subTest(table=table):
                    self.assertEqual(set(paths[path]), {"get"})

    def test_generic_tables_follow_their_area_scope(self):
        url = f"/workspaces/{self.team_a}/milestones"
        read = self.token_headers(self.team_a, t.SCOPE_PLANNING_READ)
        write = self.token_headers(self.team_a, t.SCOPE_PLANNING_WRITE)
        self.assertEqual(self.client.get(url, headers=read).status_code, 200)
        self.assertEqual(self.client.get(url, headers=write).status_code, 403)
        denied = self.client.post(url, json={}, headers=read)
        self.assertEqual(denied.status_code, 403, denied.text)
        other = self.client.get(f"/workspaces/{self.team_b}/milestones", headers=read)
        self.assertEqual(other.status_code, 403)

    def test_unlisted_tables_stay_backend_only(self):
        admin = self.token_headers(self.team_a, t.SCOPE_ADMIN)
        for table in ("events", "invitations", "notifications"):
            with self.subTest(table=table):
                response = self.client.get(f"/workspaces/{self.team_a}/{table}", headers=admin)
                self.assertEqual(response.status_code, 403, response.text)

    def test_parent_scoped_tables_stay_backend_only(self):
        from app.api import generic_crud

        gone = {"channel_members", "doc_blocks", "doc_versions", "reactions", "attachments"}
        gone |= {"meeting_participants", "transcripts", "transcript_segments", "memory_links"}
        gone |= {"chunks", "decisions", "agent_runs", "atom_connections", "links"}
        self.assertFalse(gone & set(generic_crud.ACCESS))
        collab = self.token_headers(self.team_a, t.SCOPE_COLLAB_WRITE)
        for table in ("channel-members", "transcripts"):
            with self.subTest(table=table):
                url = f"/workspaces/{self.team_a}/{table}"
                self.assertEqual(self.client.get(url, headers=collab).status_code, 403)
