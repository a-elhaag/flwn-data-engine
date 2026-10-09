"""Model calls over plain HTTP (httpx): chat, embeddings and reranking on Azure AI Foundry.

One endpoint (AZURE_FOUNDRY_ENDPOINT, the resource's services.ai.azure.com address) and one
key serve every model. Deployments are chosen by name in the request body.
"""

import base64
import json
import re
from collections import Counter
from dataclasses import dataclass
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
SPEECH_API_VERSION = "2024-11-15"
SPEECH_TIMEOUT = httpx.Timeout(600.0, connect=10.0)
_speech_limiter = RateLimiter(rate=1, capacity=3)


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


@dataclass(frozen=True)
class Phrase:
    speaker: int | None  # 1, 2, ... when the model told voices apart
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class Transcription:
    phrases: list[Phrase]
    duration_ms: int
    language: str | None


def transcribe(data: bytes, name: str, mime_type: str) -> Transcription:
    """Speech to text with speaker labels (Azure Speech fast transcription, up to 300 MB / 2 h).

    It listens for each language in SPEECH_LOCALES and picks per phrase. Quota errors (429) and
    server errors are retried; the audio is sent again each time.
    """
    definition = {
        "locales": [x.strip() for x in settings.SPEECH_LOCALES.split(",") if x.strip()],
        "diarization": {"enabled": True, "maxSpeakers": settings.SPEECH_MAX_SPEAKERS},
    }

    def call() -> dict:
        response = _http().post(
            "/speechtotext/transcriptions:transcribe",
            params={"api-version": SPEECH_API_VERSION},
            headers={"Ocp-Apim-Subscription-Key": settings.AZURE_FOUNDRY_KEY},
            files={"audio": (name, data, mime_type)},
            data={"definition": json.dumps(definition)},
            timeout=SPEECH_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    reply = call_with_retries(call, "speech.transcribe", _speech_limiter, attempts=3, backoff=5.0)
    phrases = [
        Phrase(
            item.get("speaker"),
            int(item["offsetMilliseconds"]),
            int(item["offsetMilliseconds"]) + int(item.get("durationMilliseconds", 0)),
            item["text"].strip(),
        )
        for item in reply.get("phrases", [])
        if item.get("text", "").strip()
    ]
    languages = Counter(
        item.get("locale") for item in reply.get("phrases", []) if item.get("locale")
    )
    return Transcription(
        phrases,
        int(reply.get("durationMilliseconds", phrases[-1].end_ms if phrases else 0)),
        languages.most_common(1)[0][0] if languages else None,
    )
