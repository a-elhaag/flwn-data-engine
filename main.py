"""Data Engine application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html

from api.routes import router
from clients import vector_store
from mcp_server import build_app, create_mcp


SWAGGER_UI_PARAMETERS = {
    "docExpansion": "full",
    "defaultModelsExpandDepth": 20,
    "defaultModelExpandDepth": 20,
}


def create_app() -> FastAPI:
    mcp = create_mcp()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        vector_store.ensure_collection()
        async with mcp.session_manager.run():
            yield

    app = FastAPI(
        title="flwn-data-engine",
        lifespan=lifespan,
        swagger_ui_parameters=SWAGGER_UI_PARAMETERS,
    )

    @app.get("/swagger", include_in_schema=False)
    async def swagger_ui():
        return get_swagger_ui_html(
            openapi_url=app.openapi_url,
            title=f"{app.title} - Swagger UI",
            swagger_ui_parameters=SWAGGER_UI_PARAMETERS,
        )

    app.include_router(router)
    # MCP last: it owns exactly /mcp and falls through to 404 for anything else.
    app.mount("/", build_app(mcp))
    return app


app = create_app()
