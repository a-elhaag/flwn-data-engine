import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from harness import MemoryHarness  # sets test env; import before app modules

from clients import vector_store


class MemoryApiTests(MemoryHarness):
    def test_round_trip_and_workspace_isolation(self):
        point_id = self.remember("team-a")
        other_id = self.remember("team-b")
        self.assertEqual(
            [point["id"] for point in self.recall("team-a").json()], [point_id]
        )
        self.assertEqual(
            [point["id"] for point in self.recall("team-b").json()], [other_id]
        )
        point = self.qdrant.retrieve("memory", ids=[point_id])[0]
        self.assertEqual(point.payload["recall_count"], 1)
        self.assertIsNotNone(point.payload["last_recalled_at"])
        self.assertEqual(
            self.client.delete(f"/workspaces/team-b/memories/{point_id}").status_code,
            204,
        )
        self.assertEqual(len(self.recall("team-a").json()), 1)
        self.client.delete(f"/workspaces/team-a/memories/{point_id}")
        self.assertEqual(self.recall("team-a").json(), [])

    def test_recall_preserves_recency_ranking_and_limit(self):
        old_id = self.remember("team-a")
        recent_id = self.remember("team-a")
        now = time.time()
        self.qdrant.set_payload(
            "memory", {"timestamp": now - 30 * 86400}, points=[old_id]
        )
        self.qdrant.set_payload("memory", {"timestamp": now}, points=[recent_id])
        with patch("retrieval.search.time.time", return_value=now):
            response = self.client.post(
                "/workspaces/team-a/memories/recall",
                json={"query": "fact", "agent": "reviewer", "limit": 1},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([point["id"] for point in response.json()], [recent_id])
        self.assertAlmostEqual(response.json()[0]["score"], 1.0)
        old, recent = self.qdrant.retrieve("memory", ids=[old_id, recent_id])
        self.assertEqual(old.payload["recall_count"], 0)
        self.assertEqual(recent.payload["recall_count"], 1)

    def test_cleanup_preserves_other_workspaces_and_recalled_memories(self):
        old_id = self.remember("team-a")
        live_id = self.remember("team-a")
        other_id = self.remember("team-b")
        self.qdrant.set_payload(
            "memory",
            {"timestamp": time.time() - 60 * 86400},
            points=[old_id, live_id, other_id],
        )
        vector_store.touch(live_id, "team-a", time.time())
        response = self.client.post(
            "/workspaces/team-a/sprint-completed", json={"retention_days": 30}
        )
        self.assertEqual(response.json()["deleted"], 1)
        self.assertEqual(
            {
                point.id
                for point in self.qdrant.retrieve(
                    "memory", ids=[old_id, live_id, other_id]
                )
            },
            {live_id, other_id},
        )

    def test_authentication_and_input_validation(self):
        body = {"query": "fact", "agent": "planner"}
        for key in ("", "wrong"):
            response = self.client.post(
                "/workspaces/team-a/memories/recall",
                json=body,
                headers={"X-Data-API-Key": key},
            )
            self.assertEqual(response.status_code, 401)
        self.assertEqual(
            self.client.post("/workspaces/%20/memories/recall", json=body).status_code,
            422,
        )
        for limit in (0, 101):
            self.assertEqual(
                self.client.post(
                    "/workspaces/team-a/memories/recall", json={**body, "limit": limit}
                ).status_code,
                422,
            )
        self.assertEqual(
            self.client.post(
                "/workspaces/team-a/memories/recall", json={**body, "query": " "}
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.post(
                "/workspaces/team-a/memories/cleanup", json={"retention_days": -1}
            ).status_code,
            422,
        )
        for workspace in ("", " "):
            with self.assertRaises(TypeError):
                vector_store.search([1.0, 0.0, 0.0], workspace, 5)

    def test_ai_adapter_round_trip(self):
        ai_path = str(Path(__file__).resolve().parents[2] / "flwn-ai-engine")
        with patch.object(sys, "path", [ai_path, *sys.path]), patch.dict(sys.modules):
            sys.modules.pop("config", None)
            from agents.memory_steward.memory_steward import MemorySteward
            from config import settings as ai_settings

            def request(method, url, **kwargs):
                return self.client.request(
                    method, url, headers=kwargs["headers"], json=kwargs["json"]
                )

            with patch.object(
                ai_settings, "DATA_BASE_URL", "http://testserver"
            ), patch.object(ai_settings, "DATA_API_KEY", "test-data-key"), patch(
                "httpx.request", side_effect=request
            ):
                memory = MemorySteward("team-a")
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
                self.assertEqual(MemorySteward("team-b").recall("fact", "reviewer"), [])
                self.assertEqual(memory.run_cleanup(), 0)
                self.assertEqual(memory.on_sprint_completed(), 0)
                memory.forget(point_id)
                self.assertEqual(memory.recall("fact", "reviewer"), [])


if __name__ == "__main__":
    unittest.main()
