import unittest
import uuid

from harness import MemoryHarness

from app.api import tokens
from app.storage.blobs import BlobInfo, get_storage
from app.storage.files import MB, container_for, safe_name


class FakeBlobs:
    """Stands in for BlobStorage: records calls and returns predictable links."""

    def __init__(self):
        self.stored: dict[tuple[str, str], BlobInfo] = {}
        self.deleted: list[tuple[str, str]] = []

    def upload_url(self, container, path, ttl):
        return f"https://fake.blob/{container}/{path}?sig=upload&ttl={ttl}"

    def download_url(self, container, path, ttl, filename=None):
        return f"https://fake.blob/{container}/{path}?sig=read&ttl={ttl}&name={filename}"

    def info(self, container, path):
        return self.stored.get((container, path))

    def delete(self, container, path):
        self.deleted.append((container, path))
        self.stored.pop((container, path), None)

    def upload(self, ticket, size, content_type="application/octet-stream"):
        """What the client does: PUT the bytes to the ticket's URL."""
        container, path = (
            ticket["upload_url"].split("https://fake.blob/")[1].split("?")[0].split("/", 1)
        )
        self.stored[(container, path)] = BlobInfo(size, content_type)
        return container, path


class FileHelpersTest(unittest.TestCase):
    def test_safe_name_strips_directories_and_odd_characters(self):
        self.assertEqual(safe_name("../../etc/passwd"), "passwd")
        self.assertEqual(safe_name("C:\\Users\\me\\report final.pdf"), "report final.pdf")
        self.assertEqual(safe_name("a<b>:c?.txt"), "a_b__c_.txt")
        self.assertEqual(safe_name("  ...  "), "file")
        self.assertEqual(safe_name("تقرير.pdf"), "تقرير.pdf")
        self.assertLessEqual(len(safe_name("x" * 500)), 120)

    def test_each_kind_of_content_has_its_own_container(self):
        self.assertEqual(container_for("pdf", "workspace"), "workspace-files")
        self.assertEqual(container_for("voice_note", "chat"), "chat-media")
        self.assertEqual(container_for("recording", "meeting"), "meeting-recordings")
        self.assertEqual(container_for("report", "agent"), "agent-reports")


class FilesApiTests(MemoryHarness):
    def setUp(self):
        super().setUp()
        self.blobs = FakeBlobs()
        self.client.app.dependency_overrides[get_storage] = lambda: self.blobs

    def start(self, workspace=None, **overrides):
        body = {
            "kind": "voice_note",
            "name": "standup.opus",
            "content_type": "audio/ogg",
            "size_bytes": 4096,
            "source": "chat",
            **overrides,
        }
        return self.client.post(f"/workspaces/{workspace or self.team_a}/files", json=body)

    def row(self, file_id):
        (row,) = self.sql("select * from files where id = :i", i=file_id)
        return dict(row)

    def test_upload_flow_from_start_to_download_and_delete(self):
        started = self.start()
        self.assertEqual(started.status_code, 201, started.text)
        ticket = started.json()
        self.assertEqual(ticket["method"], "PUT")
        self.assertEqual(ticket["headers"]["x-ms-blob-type"], "BlockBlob")
        row = self.row(ticket["file_id"])
        self.assertEqual((row["status"], row["container"]), ("uploading", "chat-media"))
        self.assertTrue(row["blob_path"].startswith(f"{self.team_a}/{ticket['file_id']}/"))

        # not uploaded yet: completing is a conflict, not a success
        base = f"/workspaces/{self.team_a}/files/{ticket['file_id']}"
        self.assertEqual(self.client.post(f"{base}/complete").status_code, 409)
        self.assertEqual(self.client.get(f"{base}/download").status_code, 409)

        self.blobs.upload(ticket, 4096, "audio/ogg")
        done = self.client.post(f"{base}/complete")
        self.assertEqual(done.status_code, 200, done.text)
        self.assertEqual((done.json()["status"], done.json()["size_bytes"]), ("ready", 4096))
        self.assertEqual(
            self.client.post(f"{base}/complete").json()["status"], "ready"
        )  # idempotent

        link = self.client.get(f"{base}/download").json()
        self.assertIn("sig=read", link["url"])
        self.assertIn("name=standup.opus", link["url"])
        listed = self.client.get(f"/workspaces/{self.team_a}/files").json()["items"]
        self.assertEqual([f["id"] for f in listed], [ticket["file_id"]])

        self.assertEqual(self.client.delete(base).status_code, 204)
        self.assertEqual(self.client.get(base).status_code, 404)
        self.assertEqual(self.client.get(f"/workspaces/{self.team_a}/files").json()["items"], [])
        self.assertEqual(len(self.blobs.deleted), 1)

    def test_files_route_to_the_right_container(self):
        for kind, source, container in (
            ("pdf", "workspace", "workspace-files"),
            ("recording", "meeting", "meeting-recordings"),
            ("report", "agent", "agent-reports"),
            ("image", "chat", "chat-media"),
        ):
            ticket = self.start(kind=kind, source=source, name="x.bin").json()
            self.assertEqual(self.row(ticket["file_id"])["container"], container, kind)

    def test_oversized_and_mismatched_uploads_are_rejected(self):
        too_big = self.start(kind="image", size_bytes=26 * MB, source="workspace")
        self.assertEqual(too_big.status_code, 422)
        self.assertIn("at most 25 MB", too_big.json()["detail"])

        ticket = self.start().json()
        container, path = self.blobs.upload(ticket, 9999)  # announced 4096, sent 9999
        response = self.client.post(f"/workspaces/{self.team_a}/files/{ticket['file_id']}/complete")
        self.assertEqual(response.status_code, 422)
        row = self.row(ticket["file_id"])
        self.assertEqual(row["status"], "failed")
        self.assertIn("9999", row["error"])
        self.assertIn((container, path), self.blobs.deleted)

    def test_names_cannot_escape_the_workspace_folder(self):
        ticket = self.start(name="../../other-workspace/secret.txt").json()
        path = self.row(ticket["file_id"])["blob_path"]
        self.assertEqual(path, f"{self.team_a}/{ticket['file_id']}/secret.txt")

    def test_workspaces_cannot_see_each_others_files(self):
        ticket = self.start().json()
        self.blobs.upload(ticket, 4096)
        for call in (
            self.client.get(f"/workspaces/{self.team_b}/files/{ticket['file_id']}"),
            self.client.post(f"/workspaces/{self.team_b}/files/{ticket['file_id']}/complete"),
            self.client.get(f"/workspaces/{self.team_b}/files/{ticket['file_id']}/download"),
            self.client.delete(f"/workspaces/{self.team_b}/files/{ticket['file_id']}"),
        ):
            self.assertEqual(call.status_code, 404)
        self.assertEqual(self.client.get(f"/workspaces/{self.team_b}/files").json()["items"], [])
        self.assertEqual(self.row(ticket["file_id"])["status"], "uploading")  # untouched

    def test_unknown_workspace_project_and_malformed_input(self):
        ghost = self.start(workspace=str(uuid.uuid4()))
        self.assertEqual((ghost.status_code, ghost.json()["detail"]), (404, "Workspace not found"))
        bad_ref = self.start(project_id=str(uuid.uuid4()))
        self.assertEqual(bad_ref.status_code, 422)
        self.assertIn("not found in this workspace", bad_ref.json()["detail"])
        for overrides in ({"kind": "exe"}, {"size_bytes": 0}, {"name": " "}, {"source": "web"}):
            self.assertEqual(self.start(**overrides).status_code, 422, overrides)
        self.assertEqual(self.client.get(f"/workspaces/{self.team_a}/files/junk").status_code, 422)

    def test_agent_tokens_need_file_scopes_and_their_own_workspace(self):
        def agent(workspace, scopes):
            token, _ = tokens.mint(workspace, scopes, "agent", 600)
            return {"Authorization": f"Bearer {token}", "X-Data-API-Key": ""}

        body = {"kind": "report", "name": "r.md", "content_type": "text/markdown", "size_bytes": 10}
        path = f"/workspaces/{self.team_a}/files"
        read_only = agent(self.team_a, {tokens.SCOPE_FILES_READ})
        self.assertEqual(self.client.post(path, json=body, headers=read_only).status_code, 403)
        self.assertEqual(self.client.get(path, headers=read_only).status_code, 200)
        memory_only = agent(self.team_a, {tokens.SCOPE_READ, tokens.SCOPE_WRITE})
        self.assertEqual(self.client.get(path, headers=memory_only).status_code, 403)
        writer = agent(self.team_a, {tokens.SCOPE_FILES_WRITE})
        self.assertEqual(self.client.post(path, json=body, headers=writer).status_code, 201)
        elsewhere = agent(self.team_b, tokens.AGENT_SCOPES)
        self.assertEqual(self.client.post(path, json=body, headers=elsewhere).status_code, 403)

    def test_file_routes_report_missing_storage_configuration(self):
        self.client.app.dependency_overrides.clear()
        get_storage.cache_clear()
        response = self.start()
        self.assertEqual(
            (response.status_code, response.json()["detail"]),
            (503, "File storage is not configured"),
        )


if __name__ == "__main__":
    unittest.main()
