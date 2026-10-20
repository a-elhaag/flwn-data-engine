"""Readiness checks for the database and inference."""

import logging

from sqlalchemy import text

from app.clients import inference
from app.config import settings
from app.db import session
from app.storage import blobs
from app.storage.blobs import get_storage

logger = logging.getLogger(__name__)


def health_check() -> dict[str, bool]:
    status = {"database": False, "foundry": False}
    if settings.AZURE_STORAGE_ACCOUNT_URL:
        status["storage"] = False
    try:
        with session.engine().connect() as conn:
            status["database"] = (
                conn.execute(text("select 1 from pg_extension where extname = 'vector'")).scalar()
                is not None
            )
    except Exception as exc:
        logger.warning("health_check: database unreachable: %s", exc)
    if "storage" in status:
        try:
            get_storage().info(blobs.WORKSPACE_FILES, "readiness-probe")
            status["storage"] = True
        except Exception as exc:
            logger.warning("health_check: storage unreachable: %s", exc)
    try:
        inference.embed("ping")
        status["foundry"] = True
    except Exception as exc:
        logger.warning("health_check: foundry unreachable: %s", exc)
    return status
