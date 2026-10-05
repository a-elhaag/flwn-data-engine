"""Data Engine application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.routes import router
from clients import vector_store
from mcp_server import build_app, create_mcp


def create_app() -> FastAPI:
    mcp = create_mcp()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        vector_store.ensure_collection()
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="flwn-data-engine", lifespan=lifespan)
    app.include_router(router)
    # MCP last: it owns exactly /mcp and falls through to 404 for anything else.
    app.mount("/", build_app(mcp))
    return app


app = create_app()
