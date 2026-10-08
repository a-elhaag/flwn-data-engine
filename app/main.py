"""Data Engine application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import errors
from app.api.routes import router
from app.config import settings
from app.mcp_tools.server import build_app, create_mcp
from app.storage import indexer
from app.storage.blobs import get_storage


def create_app() -> FastAPI:
    mcp = create_mcp()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Index uploaded files in the background (needs storage; any replica may run a worker).
        if settings.FILE_INDEXING and settings.AZURE_STORAGE_ACCOUNT_URL:
            indexer.start_worker(get_storage())
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            indexer.stop_worker()

    app = FastAPI(title="flwn-data-engine", lifespan=lifespan)
    errors.register(app)
    app.include_router(router)
    # MCP last: it owns exactly /mcp and falls through to 404 for anything else.
    app.mount("/", build_app(mcp))
    return app


app = create_app()
