"""Query rewriting, vector search, and recency ranking."""

import logging
import math
import time
from dataclasses import dataclass

from clients import inference, vector_store

logger = logging.getLogger(__name__)
RECENCY_HALF_LIFE_SECONDS = 14 * 24 * 60 * 60


@dataclass
class RankedResult:
    id: str
    text: str
    source: str
    agent: str
    score: float


def _recency_decay(age_seconds: float) -> float:
    return math.exp(-age_seconds / RECENCY_HALF_LIFE_SECONDS)


def recall(workspace_id: str, query: str, agent: str, limit: int = 5) -> list[RankedResult]:
    rewritten = inference.chat(
        "memory_steward.query_rewrite",
        f"Rewrite this search query to be clearer and more complete, "
        f"resolving any vague references. Reply with only the rewritten query:\n\n{query}",
    )
    candidates = vector_store.search(
        inference.embed(rewritten), workspace_id, limit=limit * 3
    )
    now = time.time()
    ranked = sorted(
        candidates,
        key=lambda point: point.score
        * _recency_decay(now - point.payload["timestamp"]),
        reverse=True,
    )[:limit]
    for point in ranked:
        vector_store.touch(point.id, workspace_id, recalled_at=now)
    logger.info(
        "recall: workspace=%s agent=%s returned=%d",
        workspace_id,
        agent,
        len(ranked),
    )
    return [
        RankedResult(
            id=str(point.id),
            text=point.payload["text"],
            source=point.payload["source"],
            agent=point.payload["agent"],
            score=point.score,
        )
        for point in ranked
    ]
