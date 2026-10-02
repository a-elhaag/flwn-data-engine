"""Data-owned compression, embeddings, recall ranking and memory maintenance."""

import logging
import math
import re
import time
from dataclasses import dataclass
from functools import lru_cache

from azure.ai.inference import ChatCompletionsClient, EmbeddingsClient
from azure.core.credentials import AzureKeyCredential

from flwn_data import vector_store
from flwn_data.config import settings
from flwn_data.resilience import RateLimiter, call_with_retries

logger = logging.getLogger(__name__)
RECENCY_HALF_LIFE_SECONDS = 14 * 24 * 60 * 60
_SPECIAL_TOKEN_RE = re.compile(r"<\|START_TEXT\|>|<\|END_TEXT\|>")
_foundry_rate_limiter = RateLimiter(rate=5, capacity=10)


@dataclass
class RankedResult:
    id: str
    text: str
    source: str
    agent: str
    score: float


@lru_cache
def chat_client() -> ChatCompletionsClient:
    return ChatCompletionsClient(
        endpoint=f"{settings.AZURE_FOUNDRY_ENDPOINT.rstrip('/')}/models",
        credential=AzureKeyCredential(settings.AZURE_FOUNDRY_KEY),
    )


@lru_cache
def embeddings_client() -> EmbeddingsClient:
    return EmbeddingsClient(
        endpoint=f"{settings.AZURE_FOUNDRY_ENDPOINT.rstrip('/')}/models",
        credential=AzureKeyCredential(settings.AZURE_FOUNDRY_KEY),
    )


def _embed(text: str) -> list[float]:
    return call_with_retries(
        lambda: embeddings_client()
        .embed(input=[text], model=settings.EMBEDDING_DEPLOYMENT)
        .data[0]
        .embedding,
        description="embeddings_client.embed",
        limiter=_foundry_rate_limiter,
    )


def _chat(task: str, prompt: str) -> str:
    def call() -> str:
        return (
            chat_client()
            .complete(
                messages=[{"role": "user", "content": prompt}],
                model=settings.MEMORY_CHAT_DEPLOYMENT,
            )
            .choices[0]
            .message.content
        )

    raw = call_with_retries(
        call, description=f"chat_client.complete[{task}]", limiter=_foundry_rate_limiter
    )
    return _SPECIAL_TOKEN_RE.sub("", raw).strip()


def _recency_decay(age_seconds: float) -> float:
    return math.exp(-age_seconds / RECENCY_HALF_LIFE_SECONDS)


def health_check() -> dict[str, bool]:
    status = {"qdrant": False, "foundry": False}
    try:
        vector_store._client().get_collections()
        status["qdrant"] = True
    except Exception as exc:
        logger.warning("health_check: qdrant unreachable: %s", exc)
    try:
        embeddings_client().embed(input=["ping"], model=settings.EMBEDDING_DEPLOYMENT)
        status["foundry"] = True
    except Exception as exc:
        logger.warning("health_check: foundry unreachable: %s", exc)
    return status


class MemorySteward:
    def __init__(self, workspace_id: str):
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise TypeError("workspace_id is required")
        self.workspace_id = workspace_id

    def remember(self, text: str, source: str, agent: str) -> str:
        compressed = _chat(
            "memory_steward.compress",
            f"Extract the key fact or decision from this, concisely:\n\n{text}",
        )
        point_id = vector_store.upsert(
            embedding=_embed(compressed),
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
        rewritten = _chat(
            "memory_steward.query_rewrite",
            f"Rewrite this search query to be clearer and more complete, "
            f"resolving any vague references. Reply with only the rewritten query:\n\n{query}",
        )
        candidates = vector_store.search(
            _embed(rewritten), self.workspace_id, limit=limit * 3
        )
        now = time.time()
        ranked = sorted(
            candidates,
            key=lambda point: point.score
            * _recency_decay(now - point.payload["timestamp"]),
            reverse=True,
        )[:limit]
        for point in ranked:
            vector_store.touch(point.id, self.workspace_id, recalled_at=now)
        logger.info(
            "recall: workspace=%s agent=%s returned=%d",
            self.workspace_id,
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

    def forget(self, point_id: str) -> None:
        vector_store.delete([point_id], self.workspace_id)

    def run_cleanup(self, retention_days: int = 30) -> int:
        cutoff = time.time() - retention_days * 24 * 60 * 60
        candidates = vector_store.find_cleanup_candidates(self.workspace_id, cutoff)
        to_delete = []
        for record in candidates:
            answer = _chat(
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
