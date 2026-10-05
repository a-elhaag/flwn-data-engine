import os
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("DATA_API_KEY", "test-data-key")
os.environ.setdefault("AZURE_FOUNDRY_ENDPOINT", "https://example.invalid")
os.environ.setdefault("AZURE_FOUNDRY_KEY", "test-foundry-key")

from fastapi.testclient import TestClient
from qdrant_client import QdrantClient

from clients import vector_store
from config import settings
from main import create_app

TOKEN_SECRET = "t" * 40


class ChatStub:
    """Task-aware stand-in for inference.chat. Override replies per task."""

    def __init__(self):
        self.replies = {
            "memory_steward.compress": '{"fact": "remembered fact", "importance": 3}',
            "memory_steward.query_rewrite": "rewritten query",
            "memory_steward.cleanup_relevance": None,  # None -> drop everything
            "memory_steward.organize": '{"action": "distinct"}',
        }

    def __call__(self, task, prompt):
        reply = self.replies[task]
        if reply is None:
            refs = [part.split('"')[1] for part in prompt.split("ref=")[1:]]
            return (
                '{"decisions": ['
                + ",".join(f'{{"ref": "{r}", "keep": false}}' for r in refs)
                + "]}"
            )
        return reply(prompt) if callable(reply) else reply


class MemoryHarness(unittest.TestCase):
    def setUp(self):
        self.chat = ChatStub()
        self.qdrant = QdrantClient(":memory:")
        self.addCleanup(self.qdrant.close)
        for mock_patch in (
            patch("clients.vector_store._client", return_value=self.qdrant),
            patch("clients.vector_store.EMBEDDING_SIZE", 3),
            patch("clients.inference.embed", return_value=[1.0, 0.0, 0.0]),
            patch(
                "clients.inference.embed_many",
                side_effect=lambda texts: [[1.0, 0.0, 0.0] for _ in texts],
            ),
            patch("clients.inference.chat", side_effect=self.chat),
            patch.object(settings, "MEMORY_DEDUP_THRESHOLD", 1.1),
            patch.object(settings, "MEMORY_TOKEN_SECRET", TOKEN_SECRET),
        ):
            mock_patch.start()
            self.addCleanup(mock_patch.stop)
        self.client = TestClient(create_app(), headers={"X-Data-API-Key": "test-data-key"})
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def remember(self, workspace):
        response = self.client.post(
            f"/workspaces/{workspace}/memories",
            json={
                "text": "raw text",
                "source": "chat",
                "agent": "planner",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["point_id"]

    def recall(self, workspace):
        return self.client.post(
            f"/workspaces/{workspace}/memories/recall",
            json={
                "query": "fact",
                "agent": "reviewer",
                "limit": 5,
            },
        )
