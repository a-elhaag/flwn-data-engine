"""Data Engine application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse

from app.api import errors
from app.api import explorer as explorer_page
from app.api.examples import EXAMPLES
from app.api.operations import OPERATIONS
from app.api.routes import router
from app.config import settings
from app.mcp_tools.server import build_app, create_mcp
from app.storage import indexer
from app.storage.blobs import get_storage

DESCRIPTION = """
REST API for the flwn data engine: workspace memory, files, meetings, atoms, planning and collaboration.

**Authentication.** Two kinds of caller:

- **Agents** send `Authorization: Bearer <token>`. Mint a token with `POST /auth/tokens`; its scopes
  decide which endpoints it may call, and it is locked to one workspace.
- **The trusted backend** sends the service key in `X-Data-Api-Key`, and `X-Acting-Member-Id` to act
  for a member. Endpoints marked "Trusted backend only" accept nothing else.

**Reading this page.** Endpoints are grouped by tag and collapsed. Open a group, then an
endpoint, to see its parameters, an example request body and the response schemas.
All request and response models are listed under **Schemas** at the bottom.
"""

TAGS = [
    {"name": "System", "description": "Health checks and token minting."},
    {
        "name": "Memory",
        "description": "Write, recall, browse, revise, pin and forget workspace memory.",
    },
    {"name": "Files", "description": "Upload, search, download and index workspace files."},
    {"name": "Meetings", "description": "Meetings, consent, recordings and transcripts."},
    {
        "name": "Decision conflicts",
        "description": "Flag and resolve proposals that contradict past decisions.",
    },
    {
        "name": "Atoms",
        "description": "Scheduled agents: versions, grants, schedules, connections and runs.",
    },
    {"name": "Approvals", "description": "Human approval of atom actions, then claim and outcome."},
    {"name": "Skills", "description": "Promote and delete local skills. Trusted backend only."},
    {"name": "Workspaces", "description": "Create and manage workspaces. Trusted backend only."},
    {"name": "Identity", "description": "Members and teams in a workspace. Trusted backend only."},
    {"name": "Planning", "description": "Projects, work items and tasks. Trusted backend only."},
    {
        "name": "Collaboration",
        "description": "Comments, docs and blocks, channels and messages. Trusted backend only.",
    },
]

SWAGGER_UI_PARAMETERS = {
    "docExpansion": "none",  # groups and endpoints start collapsed
    "defaultModelsExpandDepth": 1,  # Schemas section visible, each model collapsed
    "defaultModelExpandDepth": 2,
    "tagsSorter": "alpha",
    "filter": True,
    "persistAuthorization": True,
    "displayRequestDuration": True,
    "tryItOutEnabled": True,
}


def _openapi(app: FastAPI) -> dict:
    if app.openapi_schema is None:
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
            tags=app.openapi_tags,
        )
        for name, example in EXAMPLES.items():
            if name in schema.get("components", {}).get("schemas", {}):
                schema["components"]["schemas"][name]["examples"] = [example]
        for path, methods in schema["paths"].items():
            for method, operation in methods.items():
                if text := OPERATIONS.get(f"{method.upper()} {path}"):
                    operation["summary"] = text[0]
                    # keep the route's own docstring: it states the rules for that endpoint
                    operation["description"] = "\n\n".join(
                        filter(None, [text[1], operation.get("description")])
                    )
        app.openapi_schema = schema
    return app.openapi_schema


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

    app = FastAPI(
        title="flwn-data-engine",
        lifespan=lifespan,
        description=DESCRIPTION,
        openapi_tags=TAGS,
        swagger_ui_parameters=SWAGGER_UI_PARAMETERS,
    )
    app.openapi = lambda: _openapi(app)
    errors.register(app)

    @app.get("/explorer", include_in_schema=False, response_class=HTMLResponse)
    def explorer():
        return explorer_page.render(app.openapi())

    @app.get("/swagger", include_in_schema=False)
    def swagger_alias():
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
