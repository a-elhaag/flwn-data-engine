"""Recall: find memories by meaning and by exact words, rerank them, then weigh recency and use.

1. The query is rewritten (one LLM call; the raw query is used if that fails).
2. Vector search and keyword search each return candidates; reciprocal rank fusion merges them,
   so a ticket id or a name the embedding misses still surfaces.
3. A reranking model scores each candidate against the query (skipped, with a fallback to
   vector similarity, if it is off or fails).
4. The relevance is weighted by recency and past use, and the best few are returned.
"""

import logging
import math
import time
import uuid
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
FUSION_K = 60  # reciprocal rank fusion constant; 60 is the standard choice


@dataclass
class RankedResult:
    id: str
    text: str
    source: str
    agent: str
    score: float  # relevance to the query: the reranker's score, or cosine similarity without it


def _recency(age_seconds: float) -> float:
    """1.0 when fresh, 0.5 after one half-life."""
    half_life = settings.RECENCY_HALF_LIFE_DAYS * 24 * 60 * 60
    return 0.5 ** (max(age_seconds, 0.0) / half_life)


def rank_score(row: Memory, relevance: float, now: float) -> float:
    """relevance x recency x usage. Old exact matches lose at most half their score;
    recalled memories age from their last recall; pinned memories never age."""
    last_used = max(
        stored_at(row), row.last_recalled_at.timestamp() if row.last_recalled_at else 0.0
    )
    recency = 1.0 if row.pinned else _recency(now - last_used)
    usage = 1 + FREQUENCY_WEIGHT * math.log1p(row.recall_count)
    return relevance * (RECENCY_FLOOR + (1 - RECENCY_FLOOR) * recency) * usage


def fuse(*rankings: list[Memory]) -> list[Memory]:
    """Reciprocal rank fusion: a memory scores higher the nearer the top of any list it sits."""
    scores: dict[uuid.UUID, float] = {}
    rows: dict[uuid.UUID, Memory] = {}
    for ranking in rankings:
        for position, row in enumerate(ranking, start=1):
            rows[row.id] = row
            scores[row.id] = scores.get(row.id, 0.0) + 1.0 / (FUSION_K + position)
    return [rows[memory_id] for memory_id in sorted(scores, key=scores.get, reverse=True)]


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


def relevance(
    query: str, rows: list[Memory], similarity: dict[uuid.UUID, float]
) -> dict[uuid.UUID, float]:
    """How relevant each candidate is: the reranker's score, else cosine similarity."""
    if settings.MEMORY_RERANK and len(rows) > 1:
        try:
            scored = inference.rerank(query, [row.text for row in rows])
            return {rows[index].id: score for index, score in scored}
        except Exception as exc:
            logger.warning("recall: rerank failed, using vector similarity: %s", exc)
    return {row.id: similarity.get(row.id, 0.0) for row in rows}


def recall(
    workspace_id: str,
    query: str,
    agent: str,
    limit: int = 5,
    sources: list[str] | None = None,
) -> list[RankedResult]:
    rewritten = _rewrite(query)
    vector = inference.embed(rewritten)
    now = time.time()
    breadth = min(max(limit * 3, 10), settings.RECALL_CANDIDATES)
    with session_for(workspace_id) as session:
        store = MemoryStore(session, workspace_id)
        by_meaning = store.search(vector, breadth, sources)
        by_words = store.keyword_search(rewritten, breadth, sources)
        candidates = fuse([row for row, _ in by_meaning], by_words)[: settings.RECALL_CANDIDATES]
        similarity = {row.id: score for row, score in by_meaning}
        missing = [row.id for row in candidates if row.id not in similarity]
        similarity.update(store.similarities(vector, missing))
        scores = relevance(rewritten, candidates, similarity)
        ranked = sorted(
            candidates,
            key=lambda row: rank_score(row, scores.get(row.id, 0.0), now),
            reverse=True,
        )[:limit]
        store.touch([row.id for row in ranked], now)
        results = [
            RankedResult(
                id=str(row.id),
                text=row.text,
                source=row.source_type or "",
                agent=row.agent_name or "",
                score=scores.get(row.id, 0.0),
            )
            for row in ranked
        ]
    logger.info("recall: workspace=%s agent=%s returned=%d", workspace_id, agent, len(results))
    return results
