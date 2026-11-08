"""Ranking helpers shared by memory recall, file search and atom skills."""

import logging
import uuid

from app.core.config import settings
from app.integrations import inference

logger = logging.getLogger(__name__)

FUSION_K = 60  # reciprocal rank fusion constant; 60 is the standard choice


def fuse(*rankings: list) -> list:
    """Reciprocal rank fusion: a row scores higher the nearer the top of any list it sits."""
    scores: dict[uuid.UUID, float] = {}
    rows: dict[uuid.UUID, object] = {}
    for ranking in rankings:
        for position, row in enumerate(ranking, start=1):
            rows[row.id] = row
            scores[row.id] = scores.get(row.id, 0.0) + 1.0 / (FUSION_K + position)
    return [rows[row_id] for row_id in sorted(scores, key=scores.get, reverse=True)]


def relevance(query: str, rows: list, similarity: dict[uuid.UUID, float]) -> dict[uuid.UUID, float]:
    """How relevant each candidate is: the reranker's score, else cosine similarity."""
    if settings.MEMORY_RERANK and len(rows) > 1:
        try:
            scored = inference.rerank(query, [row.text for row in rows])
            return {rows[index].id: score for index, score in scored}
        except Exception as exc:
            logger.warning("recall: rerank failed, using vector similarity: %s", exc)
    return {row.id: similarity.get(row.id, 0.0) for row in rows}
