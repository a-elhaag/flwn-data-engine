"""Query rewriting, vector search, and ranking."""

import logging
import math
import time
from dataclasses import dataclass

from clients import inference, vector_store
from config import settings
from memory import prompts

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


def rank_score(point, now: float) -> float:
    """similarity x recency x usage. Old exact matches lose at most half their score;
    recalled memories age from their last recall; pinned memories never age."""
    payload = point.payload
    last_used = max(payload["timestamp"], payload.get("last_recalled_at") or 0.0)
    recency = 1.0 if payload.get("pinned") else _recency(now - last_used)
    usage = 1 + FREQUENCY_WEIGHT * math.log1p(payload.get("recall_count", 0))
    return point.score * (RECENCY_FLOOR + (1 - RECENCY_FLOOR) * recency) * usage


def _rewrite(query: str) -> str:
    if not settings.MEMORY_QUERY_REWRITE:
        return query
    try:
        return inference.chat(
            "memory_steward.query_rewrite", prompts.rewrite_prompt(query)
        ) or query
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
    candidates = vector_store.search(
        inference.embed(_rewrite(query)), workspace_id, limit=limit * 3, sources=sources
    )
    now = time.time()
    ranked = sorted(candidates, key=lambda point: rank_score(point, now), reverse=True)[
        :limit
    ]
    vector_store.touch_many([str(point.id) for point in ranked], workspace_id, now)
    logger.info(
        "recall: workspace=%s agent=%s returned=%d", workspace_id, agent, len(ranked)
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
