"""Recall: rewrite the query, search by meaning, then rank by relevance, recency and use."""

import logging
import math
import time
from dataclasses import dataclass

from app.clients import inference
from app.config import settings
from app.db.models.memory import Memory
from app.db.session import session_for
from app.memory import prompts
from app.memory.store import MemoryStore, stored_at

logger = logging.getLogger(__name__)
RECENCY_FLOOR = 0.5
FREQUENCY_WEIGHT = 0.1


@dataclass
class RankedResult:
    id: str
    text: str
    source: str
    agent: str
    score: float


def _recency(age_seconds: float) -> float:
    """1.0 when fresh, 0.5 after one half-life."""
    half_life = settings.RECENCY_HALF_LIFE_DAYS * 24 * 60 * 60
    return 0.5 ** (max(age_seconds, 0.0) / half_life)


def rank_score(row: Memory, similarity: float, now: float) -> float:
    """similarity x recency x usage. Old exact matches lose at most half their score;
    recalled memories age from their last recall; pinned memories never age."""
    last_used = max(
        stored_at(row), row.last_recalled_at.timestamp() if row.last_recalled_at else 0.0
    )
    recency = 1.0 if row.pinned else _recency(now - last_used)
    usage = 1 + FREQUENCY_WEIGHT * math.log1p(row.recall_count)
    return similarity * (RECENCY_FLOOR + (1 - RECENCY_FLOOR) * recency) * usage


def _rewrite(query: str) -> str:
    if not settings.MEMORY_QUERY_REWRITE:
        return query
    try:
        return (
            inference.chat("memory_steward.query_rewrite", prompts.rewrite_prompt(query)) or query
        )
    except Exception as exc:
        logger.warning("recall: query rewrite failed, using raw query: %s", exc)
        return query


def recall(
    workspace_id: str,
    query: str,
    agent: str,
    limit: int = 5,
    sources: list[str] | None = None,
) -> list[RankedResult]:
    vector = inference.embed(_rewrite(query))
    now = time.time()
    with session_for(workspace_id) as session:
        store = MemoryStore(session, workspace_id)
        candidates = store.search(vector, limit * 3, sources)
        ranked = sorted(
            candidates, key=lambda pair: rank_score(pair[0], pair[1], now), reverse=True
        )[:limit]
        store.touch([row.id for row, _ in ranked], now)
        results = [
            RankedResult(
                id=str(row.id),
                text=row.text,
                source=row.source_type or "",
                agent=row.agent_name or "",
                score=score,
            )
            for row, score in ranked
        ]
    logger.info("recall: workspace=%s agent=%s returned=%d", workspace_id, agent, len(results))
    return results
