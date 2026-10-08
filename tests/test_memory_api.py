import sys
import threading
import time
import unittest
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from harness import MemoryHarness  # sets test env; import before app modules

from app.memory.steward import MemorySteward


class MemoryApiTests(MemoryHarness):
    def test_round_trip_and_workspace_isolation(self):
        a_id = self.remember(self.team_a)
        b_id = self.remember(self.team_b)
        self.assertEqual([m["id"] for m in self.recall(self.team_a).json()], [a_id])
        self.assertEqual([m["id"] for m in self.recall(self.team_b).json()], [b_id])
        row = self.row(a_id)
        self.assertEqual(row["recall_count"], 1)
        self.assertIsNotNone(row["last_recalled_at"])
        # deleting another workspace's id leaves the memory alone
        self.assertEqual(
            self.client.delete(f"/workspaces/{self.team_b}/memories/{a_id}").status_code, 204
        )
        self.assertEqual(len(self.recall(self.team_a).json()), 1)
        self.client.delete(f"/workspaces/{self.team_a}/memories/{a_id}")
        self.assertEqual(self.recall(self.team_a).json(), [])

    def test_recall_preserves_recency_ranking_and_limit(self):
        old_id = self.remember(self.team_a)
        recent_id = self.remember(self.team_a)
        now = time.time()
        self.age(old_id, 30)
        with patch("app.memory.recall.time.time", return_value=now):
            response = self.client.post(
                f"/workspaces/{self.team_a}/memories/recall",
                json={"query": "fact", "agent": "reviewer", "limit": 1},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([m["id"] for m in response.json()], [recent_id])
        self.assertAlmostEqual(response.json()[0]["score"], 1.0)
        self.assertEqual(self.row(old_id)["recall_count"], 0)
        self.assertEqual(self.row(recent_id)["recall_count"], 1)

    def test_concurrent_recalls_never_lose_a_count(self):
        memory_id = self.remember(self.team_a)
        steward = MemorySteward(self.team_a)
        errors = []

        def hammer():
            try:
                for _ in range(5):
                    steward.recall("fact", "reviewer")
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=hammer) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual(errors, [])
        self.assertEqual(self.row(memory_id)["recall_count"], 40)

    def test_cleanup_preserves_other_workspaces_and_recalled_memories(self):
        old_id = self.remember(self.team_a)
        live_id = self.remember(self.team_a)
        other_id = self.remember(self.team_b)
        for memory_id in (old_id, live_id, other_id):
            self.age(memory_id, 60)
        self.set_memory(live_id, recall_count=1, last_recalled_at=datetime.now(UTC))
        response = self.client.post(
            f"/workspaces/{self.team_a}/sprint-completed", json={"retention_days": 30}
        )
        self.assertEqual(response.json()["deleted"], 1)
        self.assertIsNone(self.row(old_id))
        self.assertIsNotNone(self.row(live_id))
        self.assertIsNotNone(self.row(other_id))

    def test_authentication_and_input_validation(self):
        body = {"query": "fact", "agent": "planner"}
        recall_path = f"/workspaces/{self.team_a}/memories/recall"
        for key in ("", "wrong"):
            response = self.client.post(recall_path, json=body, headers={"X-Data-API-Key": key})
            self.assertEqual(response.status_code, 401)
        for bad_workspace in ("%20", "team-a", "123"):
            self.assertEqual(
                self.client.post(
                    f"/workspaces/{bad_workspace}/memories/recall", json=body
                ).status_code,
                422,
                bad_workspace,
            )
        for limit in (0, 101):
            self.assertEqual(
                self.client.post(recall_path, json={**body, "limit": limit}).status_code, 422
            )
        self.assertEqual(
            self.client.post(recall_path, json={**body, "query": " "}).status_code, 422
        )
        self.assertEqual(
            self.client.post(
                f"/workspaces/{self.team_a}/memories/cleanup", json={"retention_days": -1}
            ).status_code,
            422,
        )
        for workspace in ("", " "):
            with self.assertRaises(TypeError):
                MemorySteward(workspace)
        with self.assertRaises(ValueError):
            MemorySteward("not-a-uuid")

    def test_unknown_workspace_cannot_be_written_to_and_reads_empty(self):
        ghost = str(uuid.uuid4())
        response = self.client.post(
            f"/workspaces/{ghost}/memories",
            json={"text": "t", "source": "chat", "agent": "planner"},
        )
        self.assertEqual(
            (response.status_code, response.json()), (404, {"detail": "Workspace not found"})
        )
        self.assertEqual(self.recall(ghost).json(), [])

    def test_ai_adapter_round_trip(self):
        ai_path = str(Path(__file__).resolve().parents[2] / "flwn-ai-engine")
        if not Path(ai_path).exists():
            self.skipTest("sibling flwn-ai-engine repo not present")
        with patch.object(sys, "path", [ai_path, *sys.path]), patch.dict(sys.modules):
            sys.modules.pop("config", None)
            from agents.memory_steward.memory_steward import MemorySteward as Adapter
            from config import settings as ai_settings

            if not hasattr(Adapter, "open"):
                self.skipTest("sibling flwn-ai-engine is not on the memory-steward branch")

            def request(method, url, **kwargs):
                return self.client.request(
                    method, url, headers=kwargs["headers"], json=kwargs["json"]
                )

            with (
                patch.object(ai_settings, "DATA_BASE_URL", "http://testserver"),
                patch.object(ai_settings, "DATA_API_KEY", "test-data-key"),
                patch("httpx.request", side_effect=request),
            ):
                memory = Adapter(self.team_a)
                point_id = memory.remember("text", "chat", "planner")
                self.assertEqual(memory.recall("fact", "reviewer")[0].id, point_id)
                self.assertEqual(memory.open(point_id)["raw_text"], "text")
                self.assertEqual(memory.browse(limit=5)["items"][0]["id"], point_id)
                self.assertEqual(memory.revise(point_id, "new")["text"], "new")
                self.assertTrue(memory.anchor(point_id)["pinned"])
                self.assertEqual(memory.pulse()["pinned"], 1)
                (batch,) = memory.ingest([{"text": "b", "source": "chat", "agent": "p"}])
                self.assertFalse(batch["deduplicated"])
                self.assertEqual(memory.sweep(dry_run=True)["dry_run"], True)
                self.assertEqual(memory.organize(dry_run=True)["clusters"], 0)
                memory.forget(batch["point_id"])
                self.assertEqual(Adapter(self.team_b).recall("fact", "reviewer"), [])
                self.assertEqual(memory.run_cleanup(), 0)
                self.assertEqual(memory.on_sprint_completed(), 0)
                memory.forget(point_id)
                self.assertEqual(memory.recall("fact", "reviewer"), [])


if __name__ == "__main__":
    unittest.main()
