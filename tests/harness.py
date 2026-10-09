import unittest
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import db_support
import env  # noqa: F401  (sets the environment the app needs to import)
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import settings
from app.db.session import workspace_session
from app.main import create_app

TOKEN_SECRET = "t" * 40
DIM = 1536


def unit(index: int = 0) -> list[float]:
    """A unit vector. Identical vectors have similarity 1.0, different axes 0.0."""
    vector = [0.0] * DIM
    vector[index] = 1.0
    return vector


class ChatStub:
    """Task-aware stand-in for inference.chat. Override replies per task."""

    def __init__(self):
        self.replies = {
            "memory_steward.compress": '{"fact": "remembered fact", "importance": 3}',
            "memory_steward.query_rewrite": "rewritten query",
            "memory_steward.cleanup_relevance": None,  # None -> drop everything
            "memory_steward.organize": '{"action": "distinct"}',
            "decision_ledger.judge": '{"verdicts": []}',
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
    """Real Postgres, mocked inference. Every test gets two fresh workspaces."""

    def setUp(self):
        self.engine = db_support.engine()
        self.chat = ChatStub()
        for mock_patch in (
            patch("app.db.session.engine", return_value=self.engine),
            patch("app.clients.inference.embed", return_value=unit(0)),
            patch(
                "app.clients.inference.embed_many",
                side_effect=lambda texts: [unit(0) for _ in texts],
            ),
            patch("app.clients.inference.chat", side_effect=self.chat),
            patch.object(settings, "MEMORY_DEDUP_THRESHOLD", 1.1),
            patch.object(settings, "MEMORY_RERANK", False),  # tests that want it turn it on
            patch.object(settings, "MEMORY_TOKEN_SECRET", TOKEN_SECRET),
        ):
            mock_patch.start()
            self.addCleanup(mock_patch.stop)
        self.team_a = self.workspace("team-a")
        self.team_b = self.workspace("team-b")
        self.client = TestClient(create_app(), headers={"X-Data-API-Key": "test-data-key"})
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    # -- database helpers (service session: they bypass the workspace restriction) ----

    def sql(self, statement: str, **params):
        with workspace_session(self.engine, "", service=True) as session:
            result = session.execute(text(statement), params)
            return result.mappings().all() if result.returns_rows else None

    def workspace(self, name: str) -> str:
        slug = f"{name}-{uuid.uuid4().hex[:8]}"
        (row,) = self.sql(
            "insert into workspaces (name, slug) values (:n, :s) returning id", n=name, s=slug
        )
        workspace_id = str(row["id"])
        self.addCleanup(self.sql, "delete from workspaces where id = :i", i=workspace_id)
        return workspace_id

    def member(self, workspace: str, kind: str = "HUMAN", status: str = "active") -> str:
        """A member of the workspace: a human (with a user account) or an AI agent."""
        if kind == "AI":
            sql = (
                "insert into members (workspace_id, type, name, agent_kind, status)"
                " values (:w, 'AI', 'Ghost', 'ghost_engineer', :s) returning id"
            )
            (row,) = self.sql(sql, w=workspace, s=status)
        else:
            email = f"{uuid.uuid4().hex[:10]}@example.com"
            (user,) = self.sql(
                "insert into users (email, name) values (:e, 'Test User') returning id", e=email
            )
            # members reference the user, so they go first (cleanups run last-in, first-out)
            self.addCleanup(self.sql, "delete from users where id = :i", i=user["id"])
            self.addCleanup(self.sql, "delete from members where user_id = :i", i=user["id"])
            sql = (
                "insert into members (workspace_id, user_id, type, status)"
                " values (:w, :u, 'HUMAN', :s) returning id"
            )
            (row,) = self.sql(sql, w=workspace, u=user["id"], s=status)
        return str(row["id"])

    def events(self, workspace: str, entity_type: str | None = None) -> list[dict]:
        rows = self.sql("select * from events where workspace_id = :w order by id", w=workspace)
        return [dict(r) for r in rows if entity_type in (None, r["entity_type"])]

    def row(self, memory_id: str) -> dict | None:
        rows = self.sql("select * from memories where id = :i", i=memory_id)
        return dict(rows[0]) if rows else None

    def set_memory(self, memory_id: str, **columns) -> None:
        assignments = ", ".join(f"{name} = :{name}" for name in columns)
        self.sql(f"update memories set {assignments} where id = :id", id=memory_id, **columns)

    def age(self, memory_id: str, days: float) -> None:
        self.set_memory(memory_id, created_at=datetime.now(UTC) - timedelta(days=days))

    def count(self, workspace_id: str) -> int:
        return self.sql(
            "select count(*) as n from memories where workspace_id = :w", w=workspace_id
        )[0]["n"]

    # -- API helpers --------------------------------------------------------------

    def remember(self, workspace: str) -> str:
        response = self.client.post(
            f"/workspaces/{workspace}/memories",
            json={"text": "raw text", "source": "chat", "agent": "planner"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["point_id"]

    def recall(self, workspace: str):
        return self.client.post(
            f"/workspaces/{workspace}/memories/recall",
            json={"query": "fact", "agent": "reviewer", "limit": 5},
        )
