"""Readiness checks for storage and inference."""

import logging

from clients import vector_store
from clients.inference import embeddings_client
from config import settings

logger = logging.getLogger(__name__)


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

