"""Memory extraction, persistence, and maintenance for workspace information."""

import logging
import time

from clients import inference, vector_store
from retrieval import search
from retrieval.search import RankedResult

logger = logging.getLogger(__name__)


class MemorySteward:
    def __init__(self, workspace_id: str):
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise TypeError("workspace_id is required")
        self.workspace_id = workspace_id

    def remember(self, text: str, source: str, agent: str) -> str:
        compressed = inference.chat(
            "memory_steward.compress",
            f"Extract the key fact or decision from this, concisely:\n\n{text}",
        )
        point_id = vector_store.upsert(
            embedding=inference.embed(compressed),
            workspace_id=self.workspace_id,
            text=compressed,
            source=source,
            agent=agent,
            timestamp=time.time(),
        )
        logger.info(
            "remember: workspace=%s stored point=%s", self.workspace_id, point_id
        )
        return point_id

    def recall(self, query: str, agent: str, limit: int = 5) -> list[RankedResult]:
        return search.recall(self.workspace_id, query, agent, limit)

    def forget(self, point_id: str) -> None:
        vector_store.delete([point_id], self.workspace_id)

    def run_cleanup(self, retention_days: int = 30) -> int:
        cutoff = time.time() - retention_days * 24 * 60 * 60
        candidates = vector_store.find_cleanup_candidates(self.workspace_id, cutoff)
        to_delete = []
        for record in candidates:
            answer = inference.chat(
                "memory_steward.cleanup_relevance",
                f"Is this piece of information still likely to be relevant? "
                f'Reply with only "yes" or "no".\n\n{record.payload["text"]}',
            )
            if answer.lower().startswith("no"):
                to_delete.append(str(record.id))
        if to_delete:
            vector_store.delete(to_delete, self.workspace_id)
        logger.info(
            "run_cleanup: workspace=%s removed=%d", self.workspace_id, len(to_delete)
        )
        return len(to_delete)

    def on_sprint_completed(self, retention_days: int = 30) -> int:
        return self.run_cleanup(retention_days=retention_days)
