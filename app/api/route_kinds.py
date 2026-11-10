"""Which surface each HTTP route belongs to (issue #18).

product   hand-written routes the Go backend builds the product on. In /docs.
raw       generic CRUD over one table (app/api/generic_crud.py). In /docs under the "Raw tables" tag.
internal  engine plumbing: the scheduler, the atom runner, the indexer and the AI engine. Service key
          only (a Bearer token gets 403) and hidden from /docs.

Routes with a hand-written engine path are listed in INTERNAL_ROUTES and set include_in_schema=False
where they are declared. Generic tables the engine writes are listed in INTERNAL_TABLES.
"""

from fastapi.routing import APIRoute

PRODUCT = "product"
RAW = "raw"
INTERNAL = "internal"
KINDS = (PRODUCT, RAW, INTERNAL)

GENERIC_MODULE = "app.api.generic_crud"

# Generic tables only the engine writes or reads. Hidden from /docs; service key only.
INTERNAL_TABLES = frozenset(
    {
        "events",
        "llm_usage",
        "agent_runs",
        "agent_reports",
        "chunks",
        "transcript_segments",
        "decisions",
    }
)

# Hand-written engine routes: (method, path template). Service key only, hidden from /docs.
# Internal (hidden from OpenAPI) but still reachable with a token of the named scope, by decision:
# they were token-scoped before the split and existing callers rely on it.
TOKEN_SCOPED_INTERNAL = frozenset(
    {
        ("POST", "/workspaces/{workspace_id}/memories/cleanup"),
        ("POST", "/workspaces/{workspace_id}/memories/organize"),
        ("POST", "/workspaces/{workspace_id}/sprint-completed"),
        ("POST", "/workspaces/{workspace_id}/files/{file_id}/reindex"),
    }
)
INTERNAL_ROUTES = frozenset(
    {
        ("GET", "/atoms/scheduler-workspaces"),
        ("GET", "/workspaces/{workspace_id}/atoms/scheduler-state"),
        ("POST", "/workspaces/{workspace_id}/atoms/{atom_id}/runs"),
        ("POST", "/workspaces/{workspace_id}/atoms/{atom_id}/runs/release-stale"),
        ("GET", "/workspaces/{workspace_id}/approvals/ready"),
        ("POST", "/workspaces/{workspace_id}/approvals/{approval_id}/execution-run"),
        ("POST", "/workspaces/{workspace_id}/memories/cleanup"),
        ("POST", "/workspaces/{workspace_id}/memories/organize"),
        ("POST", "/workspaces/{workspace_id}/sprint-completed"),
        ("POST", "/workspaces/{workspace_id}/files/{file_id}/reindex"),
    }
)


def kind_of(method: str, path: str, route: APIRoute) -> str:
    """The surface of one route. Every route gets exactly one kind."""
    if getattr(route.endpoint, "__module__", "") == GENERIC_MODULE:
        table = path.split("/")[3].replace("-", "_")  # /workspaces/{workspace_id}/<table>/...
        return INTERNAL if table in INTERNAL_TABLES else RAW
    return INTERNAL if (method, path) in INTERNAL_ROUTES else PRODUCT


def routes_of(app) -> list[tuple[str, str, APIRoute]]:
    """(method, path, route) for every effective route of an app, including included routers."""
    from fastapi.routing import iter_route_contexts

    found = []
    for context in iter_route_contexts(app.router.routes):
        if not isinstance(context.route, APIRoute):
            continue
        for method in sorted(context.methods):
            found.append((method, context.path, context.route))
    return found
