from harness import MemoryHarness


class CollaborationApiTests(MemoryHarness):
    def project(self, key):
        response = self.client.post(
            f"/workspaces/{self.team_a}/projects", json={"key": key, "name": key}
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_comment_replies_must_share_the_target_and_support_lifecycle(self):
        project = self.project("ALPHA")
        other_project = self.project("BETA")
        base = f"/workspaces/{self.team_a}/comments"
        created = self.client.post(
            base,
            json={"target_type": "project", "target_id": project["id"], "body": "Note"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        comment = created.json()

        reply = self.client.post(
            base,
            json={
                "target_type": "project",
                "target_id": project["id"],
                "parent_comment_id": comment["id"],
                "body": "Reply",
            },
        )
        self.assertEqual(reply.status_code, 201, reply.text)
        mismatched = self.client.post(
            base,
            json={
                "target_type": "project",
                "target_id": other_project["id"],
                "parent_comment_id": comment["id"],
                "body": "Wrong thread",
            },
        )
        self.assertEqual(mismatched.status_code, 409, mismatched.text)

        listed = self.client.get(
            base, params={"target_type": "project", "target_id": project["id"]}
        )
        self.assertEqual(len(listed.json()["items"]), 2)
        updated = self.client.patch(
            f"{base}/{comment['id']}", json={"resolved": True, "body": "Edited"}
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertIsNotNone(updated.json()["resolved_at"])
        self.assertEqual(self.client.delete(f"{base}/{comment['id']}").status_code, 204)
        self.assertEqual(self.client.get(f"{base}/{comment['id']}").status_code, 404)

    def test_docs_blocks_prevent_cycles_and_delete_child_pages(self):
        project = self.project("DOCS")
        base = f"/workspaces/{self.team_a}/docs"
        invalid_ref = "00000000-0000-4000-8000-000000000000"
        for field in ("collection_id", "cover_file_id"):
            response = self.client.post(base, json={"title": "Invalid ref", field: invalid_ref})
            self.assertEqual(response.status_code, 404, response.text)

        root_response = self.client.post(base, json={"title": "Root", "project_id": project["id"]})
        self.assertEqual(root_response.status_code, 201, root_response.text)
        root = root_response.json()
        child_response = self.client.post(
            base, json={"title": "Child", "parent_doc_id": root["id"]}
        )
        self.assertEqual(child_response.status_code, 201, child_response.text)
        child = child_response.json()

        first = self.client.post(f"{base}/{root['id']}/blocks", json={"type": "paragraph"})
        self.assertEqual(first.status_code, 201, first.text)
        first_block = first.json()
        second = self.client.post(
            f"{base}/{root['id']}/blocks",
            json={"type": "paragraph", "parent_block_id": first_block["id"]},
        )
        self.assertEqual(second.status_code, 201, second.text)
        cycle = self.client.patch(
            f"{base}/{root['id']}/blocks/{first_block['id']}",
            json={"parent_block_id": second.json()["id"]},
        )
        self.assertEqual(cycle.status_code, 409, cycle.text)

        self.assertEqual(self.client.delete(f"{base}/{root['id']}").status_code, 204)
        self.assertEqual(self.client.get(f"{base}/{root['id']}").status_code, 404)
        self.assertEqual(self.client.get(f"{base}/{child['id']}").status_code, 404)

    def test_channel_members_and_messages_support_lifecycle(self):
        member = self.member(self.team_a)
        channels = f"/workspaces/{self.team_a}/channels"
        created = self.client.post(
            channels,
            json={"type": "group", "name": "Ops", "member_ids": [member]},
        )
        self.assertEqual(created.status_code, 201, created.text)
        channel = created.json()
        messages = f"{channels}/{channel['id']}/messages"
        sent = self.client.post(messages, json={"author_id": member, "body": "Hello"})
        self.assertEqual(sent.status_code, 201, sent.text)
        self.assertEqual(sent.json()["metadata"], {})
        message = sent.json()

        pinned = self.client.patch(f"{messages}/{message['id']}", json={"pinned": True})
        self.assertEqual(pinned.status_code, 200, pinned.text)
        self.assertIsNotNone(pinned.json()["pinned_at"])
        self.assertEqual(self.client.delete(f"{messages}/{message['id']}").status_code, 204)
        self.assertEqual(self.client.get(f"{messages}/{message['id']}").status_code, 404)

        self.assertEqual(
            self.client.put(
                f"{channels}/{channel['id']}/members", json={"member_ids": []}
            ).status_code,
            200,
        )
        self.assertEqual(self.client.delete(f"{channels}/{channel['id']}").status_code, 204)
        self.assertEqual(self.client.get(f"{channels}/{channel['id']}").status_code, 404)

    def test_collaboration_routes_require_service_credentials(self):
        response = self.client.get(
            f"/workspaces/{self.team_a}/docs", headers={"X-Data-API-Key": ""}
        )
        self.assertEqual(response.status_code, 401)
