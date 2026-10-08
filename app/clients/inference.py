"""Model calls over plain HTTP (httpx): chat, embeddings and reranking on Azure AI Foundry.

One endpoint (AZURE_FOUNDRY_ENDPOINT, the resource's services.ai.azure.com address) and one
key serve every model. Deployments are chosen by name in the request body.
"""

import base64
import re
from functools import lru_cache

import httpx

from app.clients.resilience import RateLimiter, call_with_retries
from app.config import settings

API_VERSION = "2024-05-01-preview"
EMBED_BATCH_SIZE = 16
TIMEOUT = httpx.Timeout(60.0, connect=10.0)
_SPECIAL_TOKEN_RE = re.compile(r"<\|START_TEXT\|>|<\|END_TEXT\|>")
_limiter = RateLimiter(rate=5, capacity=10)
# Parse's quota is far smaller than the other models': one request, then wait.
_parse_limiter = RateLimiter(rate=1 / settings.PARSE_INTERVAL_SECONDS, capacity=1)
PARSE_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp", "image/tiff"}
PARSE_MAX_BYTES = 20 * 1024 * 1024


@lru_cache
def _http() -> httpx.Client:
    return httpx.Client(
        base_url=settings.AZURE_FOUNDRY_ENDPOINT.rstrip("/"),
        headers={"api-key": settings.AZURE_FOUNDRY_KEY},
        timeout=TIMEOUT,
    )


def _post(path: str, body: dict, description: str, *, versioned: bool = True) -> dict:
    def call() -> dict:
        response = _http().post(
            path, json=body, params={"api-version": API_VERSION} if versioned else None
        )
        response.raise_for_status()
        return response.json()

    return call_with_retries(call, description=description, limiter=_limiter)


def _embeddings(texts: list[str], description: str) -> list[list[float]]:
    data = _post(
        "/models/embeddings",
        {"input": texts, "model": settings.EMBEDDING_DEPLOYMENT},
        description,
    )["data"]
    return [item["embedding"] for item in sorted(data, key=lambda item: item["index"])]


def embed(text: str) -> list[float]:
    return _embeddings([text], "embeddings.embed")[0]


def embed_many(texts: list[str]) -> list[list[float]]:
    """Embed texts in provider-sized batches, preserving input order."""
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        vectors.extend(
            _embeddings(texts[start : start + EMBED_BATCH_SIZE], "embeddings.embed_many")
        )
    return vectors


def chat(task: str, prompt: str) -> str:
    reply = _post(
        "/models/chat/completions",
        {
            "model": settings.MEMORY_CHAT_DEPLOYMENT,
            "messages": [{"role": "user", "content": prompt}],
        },
        f"chat[{task}]",
    )["choices"][0]["message"]["content"]
    return _SPECIAL_TOKEN_RE.sub("", reply or "").strip()


def rerank(query: str, documents: list[str], top_n: int | None = None) -> list[tuple[int, float]]:
    """Order documents by relevance to the query: [(index into documents, score)], best first."""
    if not documents:
        return []
    body = {
        "model": settings.RERANK_DEPLOYMENT,
        "query": query,
        "documents": documents,
        "top_n": min(top_n or len(documents), len(documents)),
    }
    results = _post("/providers/cohere/v2/rerank", body, "rerank", versioned=False)["results"]
    return [(item["index"], float(item["relevance_score"])) for item in results]


def parse_image(data: bytes, mime_type: str = "image/png") -> str:
    """Read an image (a photo, a screenshot, a scanned page) into markdown, tables included.

    The Cohere Parse model accepts images only (not PDFs), up to 20 MB. Its quota is small and it
    sends no Retry-After, so calls are paced and a rejected one waits 10s, 20s, 40s before giving up.
    """
    if len(data) > PARSE_MAX_BYTES:
        raise ValueError("image is over the 20 MB the Parse model accepts")
    body = {
        "model": settings.PARSE_DEPLOYMENT,
        "output_format": "markdown",
        "document": {
            "type": "image_url",
            "image_url": f"data:{mime_type};base64,{base64.b64encode(data).decode()}",
        },
    }

    def call() -> dict:
        response = _http().post("/providers/cohere/v2/parse", json=body, timeout=120)
        response.raise_for_status()
        return response.json()

    reply = call_with_retries(call, "parse", _parse_limiter, attempts=4, backoff=10.0)
    return "\n\n".join(page["markdown"]["content"] for page in reply["pages"]).strip()
