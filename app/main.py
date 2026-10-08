"""Data Engine application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import errors
from app.api.routes import router
from app.mcp_tools.server import build_app, create_mcp


def create_app() -> FastAPI:
    mcp = create_mcp()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="flwn-data-engine", lifespan=lifespan)
    errors.register(app)
    app.include_router(router)
    # MCP last: it owns exactly /mcp and falls through to 404 for anything else.
    app.mount("/", build_app(mcp))
    return app


app = create_app()
