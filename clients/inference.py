"""Shared chat and embedding clients for memory and retrieval."""

import re
from functools import lru_cache

from azure.ai.inference import ChatCompletionsClient, EmbeddingsClient
from azure.core.credentials import AzureKeyCredential

from config import settings
from clients.resilience import RateLimiter, call_with_retries

_SPECIAL_TOKEN_RE = re.compile(r"<\|START_TEXT\|>|<\|END_TEXT\|>")
_foundry_rate_limiter = RateLimiter(rate=5, capacity=10)


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


def embed(text: str) -> list[float]:
    return call_with_retries(
        lambda: embeddings_client()
        .embed(input=[text], model=settings.EMBEDDING_DEPLOYMENT)
        .data[0]
        .embedding,
        description="embeddings_client.embed",
        limiter=_foundry_rate_limiter,
    )


def chat(task: str, prompt: str) -> str:
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
