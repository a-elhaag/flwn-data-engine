"""Generic CRUD boundary: domain-owned tables are not writable generically (#18), and duplicate
generic routes are gone (#29)."""

"""Phase 1 CRUD for the tables that had no routes: every table lists, the read-only ones reject
writes, and the full-CRUD ones round-trip, including a composite key."""

from harness import MemoryHarness

from app.api import generic_crud

# Every table that generic CRUD can write. Changing this list is a deliberate, reviewed decision.
WRITABLE = {
    "links",
    "favorites",
    "labels",
    "subscriptions",
    "collections",
    "folders",
    "milestones",
    "project_updates",
    "saved_views",
    "doc_versions",
    "work_item_labels",
    "task_labels",
    "task_relations",
    "reactions",
}
# Tables whose writes belong to a hand-written route or a service rule (issues #18 and #29). They
# must never be writable through generic CRUD. The value names the owner.
DOMAIN_OWNED = {
    "team_members": "PUT /teams/{team_id}/members",
    "channel_members": "PUT /channels/{channel_id}/members",
    "doc_blocks": "/docs/{doc_id}/blocks",
    "meeting_participants": "POST /meetings/{meeting_id}/participants",
    "transcripts": "PUT /meetings/{meeting_id}/transcript",
    "memory_links": "/memories",
    "backlogs": "planning service: one per project",
    "notifications": "approvals service",
}
# Owned by a hand-written route, so the generic one is removed entirely (issue #29).
REMOVED = {"team_members", "channel_members", "doc_blocks"}


class GenericCrudBoundaryTests(MemoryHarness):
    def setUp(self):
        super().setUp()
        self.service = {"X-Data-API-Key": "test-data-key"}

    def path(self, table):
        return f"/workspaces/{self.team_a}/{table.replace('_', '-')}"

    def test_writable_tables_are_the_reviewed_list(self):
        self.assertEqual(generic_crud.CRUD_TABLES, WRITABLE)

    def test_domain_owned_tables_are_classified_consistently(self):
        self.assertFalse(WRITABLE & set(DOMAIN_OWNED))
        self.assertLessEqual(set(DOMAIN_OWNED) - REMOVED, generic_crud.READ_TABLES)
        self.assertFalse(REMOVED & (generic_crud.CRUD_TABLES | generic_crud.READ_TABLES))

    def test_domain_owned_tables_have_no_write_routes(self):
        from app.main import app

        spec = app.openapi()["paths"]
        for table in sorted(set(DOMAIN_OWNED) - REMOVED):
            with self.subTest(table=table):
                # A method with no route falls through to the MCP mount, which answers 401.
                created = self.client.post(self.path(table), json={}, headers=self.service)
                self.assertGreaterEqual(created.status_code, 400, created.text)
                prefix = f"/workspaces/{{workspace_id}}/{table.replace('_', '-')}"
                for key, operations in spec.items():
                    if key == prefix or key.startswith(prefix + "/"):
                        self.assertEqual(set(operations), {"get"}, key)

    def test_removed_tables_have_no_generic_routes(self):
        from app.main import app

        paths = app.openapi()["paths"]
        for table in sorted(REMOVED):
            with self.subTest(table=table):
                self.assertNotIn(self.path(table).replace(self.team_a, "{workspace_id}"), paths)
                # Unmatched paths fall through to the MCP mount, so the status is 401 or 404.
                response = self.client.post(self.path(table), json={}, headers=self.service)
                self.assertGreaterEqual(response.status_code, 400, response.text)
                self.assertNotEqual(response.status_code, 201, response.text)

    def test_hand_written_member_route_still_works(self):
        member = self.member(self.team_a)
        team = self.client.post(
            f"/workspaces/{self.team_a}/teams",
            json={"key": "ENG", "name": "Engineering"},
            headers=self.service,
        )
        self.assertEqual(team.status_code, 201, team.text)
        response = self.client.put(
            f"/workspaces/{self.team_a}/teams/{team.json()['id']}/members",
            json={"member_ids": [member]},
            headers=self.service,
        )
        self.assertEqual(response.status_code, 200, response.text)


class GenericCrudTests(MemoryHarness):
    def setUp(self):
        super().setUp()
        self.service = {"X-Data-API-Key": "test-data-key"}

    def base(self, table, workspace=None):
        return f"/workspaces/{workspace or self.team_a}/{table.replace('_', '-')}"

    def test_every_generated_table_lists_for_its_workspace(self):
        for table in sorted(generic_crud.CRUD_TABLES | generic_crud.READ_TABLES):
            with self.subTest(table=table):
                response = self.client.get(self.base(table), headers=self.service)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["items"], [])

    def test_read_only_tables_have_no_write_routes(self):
        from app.main import app

        spec = app.openapi()["paths"]
        for table in sorted(generic_crud.READ_TABLES):
            path = f"/workspaces/{{workspace_id}}/{table.replace('_', '-')}"
            with self.subTest(table=table):
                self.assertNotIn("post", spec.get(path, {}))
                self.assertIn("get", spec[path])

    def test_label_round_trip_and_workspace_isolation(self):
        created = self.client.post(
            self.base("labels"), json={"name": "urgent", "color": "red"}, headers=self.service
        )
        self.assertEqual(created.status_code, 201, created.text)
        label = created.json()
        url = f"{self.base('labels')}/{label['id']}"
        self.assertEqual(self.client.get(url, headers=self.service).json()["name"], "urgent")
        self.assertEqual(
            self.client.patch(url, json={"name": "later"}, headers=self.service).json()["name"],
            "later",
        )
        self.assertEqual(self.client.delete(url, headers=self.service).status_code, 204)
        self.assertEqual(self.client.get(url, headers=self.service).status_code, 404)

        other = self.client.post(
            self.base("labels"), json={"name": "private"}, headers=self.service
        ).json()
        self.assertEqual(
            self.client.get(
                f"{self.base('labels', self.team_b)}/{other['id']}", headers=self.service
            ).status_code,
            404,
        )

    def test_missing_required_field_is_a_validation_error(self):
        response = self.client.post(
            self.base("labels"), json={"color": "red"}, headers=self.service
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_composite_key_round_trip(self):
        member = self.member(self.team_a)
        created = self.client.post(
            self.base("subscriptions"),
            json={"member_id": member, "entity_type": "task", "entity_id": member},
            headers=self.service,
        )
        self.assertEqual(created.status_code, 201, created.text)
        url = f"{self.base('subscriptions')}/{member}/task/{member}"
        self.assertEqual(self.client.get(url, headers=self.service).status_code, 200)
        self.assertEqual(self.client.delete(url, headers=self.service).status_code, 204)
        self.assertEqual(self.client.get(url, headers=self.service).status_code, 404)

    def test_generated_routes_refuse_a_bearer_token(self):
        response = self.client.get(
            self.base("labels"),
            headers={"Authorization": "Bearer x.y.z", "X-Data-API-Key": ""},
        )
        self.assertEqual(response.status_code, 401)

    def test_author_comes_from_the_acting_member_not_the_body(self):
        me = self.member(self.team_a)
        someone_else = self.member(self.team_a)
        headers = {**self.service, "X-Acting-Member-Id": me}
        created = self.client.post(
            self.base("folders"),
            json={"name": "mine", "created_by": someone_else},
            headers=headers,
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["created_by"], me)
        self.assertNotIn("created_by", generic_crud.FoldersRowCreate.model_fields)
        self.assertNotIn("id", generic_crud.FoldersRowUpdate.model_fields)
