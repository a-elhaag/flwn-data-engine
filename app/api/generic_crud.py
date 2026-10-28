"""Generic CRUD for the tables that have no hand-written routes yet (Phase 1).

Each table's request and response models are built from its columns, so they cannot drift from the
database. Full CRUD for the child tables in CRUD_TABLES; read-only (list and get) for the engine-
written and security-sensitive tables in READ_TABLES. Composite keys are addressed by their parts,
for example DELETE /workspaces/{ws}/team-members/{team_id}/{member_id}.

Access follows ACCESS: a token with the table's read or write scope (the area's scopes, locked to
its workspace), or the service key. Tables not in ACCESS are the trusted backend only. Every query is
filtered by workspace and runs inside the workspace's row-level-security session.
"""

import inspect
import sys
import uuid
import warnings
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi import Path as PathParam
from pydantic import ConfigDict, Field, create_model
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

import app.db.models  # noqa: F401  (registers every model on Base.metadata)
from app.api import auth, tokens
from app.api.examples import EXAMPLES
from app.db.base import Base
from app.db.session import session_for

CRUD_TABLES = {
    "links",
    "favorites",
    "labels",
    "notifications",
    "subscriptions",
    "team_members",
    "backlogs",
    "collections",
    "folders",
    "milestones",
    "project_updates",
    "saved_views",
    "channel_members",
    "memory_links",
    "doc_blocks",
    "doc_versions",
    "meeting_participants",
    "transcripts",
    "work_item_labels",
    "task_labels",
    "task_relations",
    "reactions",
}
READ_TABLES = {
    "events",
    "llm_usage",
    "agent_runs",
    "agent_reports",
    "chunks",
    "file_derivatives",
    "transcript_segments",
    "attachments",
    "atom_grants",
    "atom_versions",
    "atom_schedules",
    "atom_connections",
    "atom_skills",
    "invitations",
    "decisions",
    "catalog_skills",
}
# Never sent by clients or returned in full: the database or search machinery owns them.
HIDDEN = {"tsv", "embedding"}
SERVER_SET = {"id", "workspace_id", "created_at", "updated_at", "deleted_at"}
# Set from the caller's token, never from the body: a client cannot claim to be someone else.
AUTHOR = {"created_by", "last_edited_by"}

# table -> (read scope, write scope). A table missing here is for the trusted backend only: they
# hold per-member or security-sensitive rows (notifications, favorites, subscriptions, invitations,
# events, llm_usage, agent_reports) or inherit visibility from a private parent (channel_members,
# doc_*, reactions, attachments, meetings, memory) or leak payloads (agent_runs, atom_connections). The service key and an `admin` token pass
# every scope check.
PLANNING = (tokens.SCOPE_PLANNING_READ, tokens.SCOPE_PLANNING_WRITE)
COLLAB = (tokens.SCOPE_COLLAB_READ, tokens.SCOPE_COLLAB_WRITE)
FILES = (tokens.SCOPE_FILES_READ, tokens.SCOPE_FILES_WRITE)
ATOMS = (tokens.SCOPE_ATOMS_READ, None)  # engine-written: read only
ACCESS = {
    **dict.fromkeys(
        ("backlogs", "milestones", "project_updates", "labels", "saved_views", "task_labels")
        + ("task_relations", "work_item_labels"),
        PLANNING,
    ),
    **dict.fromkeys(("collections", "links"), COLLAB),
    **dict.fromkeys(("folders", "file_derivatives"), FILES),
    **dict.fromkeys(("atom_grants", "atom_versions", "atom_schedules", "atom_skills"), ATOMS),
    "catalog_skills": (tokens.SCOPE_SKILLS_READ, None),
}

router = APIRouter(prefix="/workspaces/{workspace_id}")
PAGE_LIMIT = Query(ge=1, le=200)
PAGE_OFFSET = Query(ge=0)


def _path(table: str) -> str:
    return "/" + table.replace("_", "-")


def _camel(table: str) -> str:
    return "".join(part.capitalize() for part in table.split("_"))


def _py_type(column) -> Any:
    try:
        return column.type.python_type
    except NotImplementedError:
        return Any


def _describe(column) -> str:
    """Built from the schema: the name, the table it links to, and whether it is required."""
    name = column.name.replace("_", " ")
    text = f"{name[:1].upper()}{name[1:]}."
    targets = sorted({fk.column.table.name for fk in column.foreign_keys})
    if targets:
        text += f" Links to `{targets[0]}`."
    text += " Optional." if column.nullable else " Required."
    return text


def _fields(columns, required: bool) -> dict:
    out = {}
    for column in columns:
        typ = _py_type(column)
        optional = not required or column.nullable or column.server_default is not None
        if optional:
            out[column.name] = (typ | None, Field(None, description=_describe(column)))
        else:
            out[column.name] = (typ, Field(..., description=_describe(column)))
    return out


def _example(columns) -> dict:
    sample = {
        uuid.UUID: "3f2b8c1e-5a47-4d0e-9c1a-7b6d2e8f4a10",
        str: "example",
        int: 1,
        float: 1.0,
        bool: False,
        datetime: "2026-01-01T00:00:00Z",
        date: "2026-01-01",
        Decimal: "0",
        dict: {},
        list: [],
    }
    out = {}
    for column in columns:
        if column.nullable or column.server_default is not None:
            continue
        typ = _py_type(column)
        out[column.name] = sample.get(typ, "example")
    return out


def _models(table) -> dict:
    """Pydantic models for one table, built from its columns."""
    name = _camel(table.name)
    visible = [c for c in table.columns if c.name not in HIDDEN]
    creatable = [c for c in visible if c.name not in SERVER_SET | AUTHOR]
    updatable = [c for c in creatable if not c.primary_key]  # a row keeps its key
    out_model = create_model(
        f"{name}Row",
        __config__=ConfigDict(from_attributes=True),
        **_fields(visible, required=False),
    )
    page = create_model(
        f"{name}RowPage",
        items=(list[out_model], Field(..., description="The rows on this page.")),
        limit=(int, Field(..., description="Page size that was used (1 to 200).")),
        offset=(int, Field(..., description="Number of rows skipped before this page.")),
        has_more=(bool, Field(..., description="True if another page follows.")),
    )
    models = {"out": out_model, "page": page}
    if table.name in CRUD_TABLES:
        models["create"] = create_model(f"{name}RowCreate", **_fields(creatable, required=True))
        models["update"] = create_model(f"{name}RowUpdate", **_fields(updatable, required=False))
        EXAMPLES[f"{name}RowCreate"] = _example(creatable)
        EXAMPLES[f"{name}RowUpdate"] = {}
    return models


def _pk(table) -> list[str]:
    return [c.name for c in table.primary_key.columns]


def _signature(params: list[inspect.Parameter]):
    return inspect.Signature(params)


def _error(exc: Exception):
    if isinstance(exc, IntegrityError):
        text = str(exc.orig).lower()
        if "check constraint" in text:
            raise HTTPException(422, "a value breaks a database rule") from None
        raise HTTPException(409, "resource conflicts with an existing record") from None
    raise exc


def _register(table, models: dict) -> dict:
    """Routes for one table. Returns the summaries to add to the operations table."""
    model = {m.persist_selectable.name: m.class_ for m in Base.registry.mappers}[table.name]
    pk_cols = {c.name: c for c in table.primary_key.columns}
    key_names = _pk(table)
    key_path = "/".join("{" + name + "}" for name in key_names)
    base = _path(table.name)
    scoped = "workspace_id" in table.columns  # catalog_skills is shared: no workspace column
    has_deleted = "deleted_at" in table.columns
    has_created = "created_at" in table.columns
    has_actor = "created_by" in table.columns
    has_editor = "last_edited_by" in table.columns
    summaries = {}
    ws_param = inspect.Parameter(
        "workspace_id",
        inspect.Parameter.KEYWORD_ONLY,
        annotation=Annotated[uuid.UUID, PathParam()],
    )
    request_param = inspect.Parameter("request", inspect.Parameter.KEYWORD_ONLY, annotation=Request)
    key_params = [
        inspect.Parameter(
            name,
            inspect.Parameter.KEYWORD_ONLY,
            annotation=Annotated[_py_type(pk_cols[name]), PathParam()],
        )
        for name in key_names
    ]
    label = table.name.replace("_", " ")
    read_scope, write_scope = ACCESS.get(table.name, (None, None))
    read_guard = auth.allow(read_scope)
    write_guard = auth.allow(write_scope)  # no write scope: the service key only, never a token

    def lookup(session, workspace_id, keys):
        query = select(model)
        if scoped:
            query = query.where(model.workspace_id == workspace_id)
        for name, value in keys.items():
            query = query.where(getattr(model, name) == value)
        if has_deleted:
            query = query.where(model.deleted_at.is_(None))
        row = session.scalar(query)
        if row is None:
            raise HTTPException(404, f"{label} not found")
        return row

    # ---- list ------------------------------------------------------------------------------
    def list_rows(workspace_id, request, limit: int = 50, offset: int = 0):
        with session_for(str(workspace_id)) as session:
            query = select(model)
            if scoped:
                query = query.where(model.workspace_id == workspace_id)
            if has_deleted:
                query = query.where(model.deleted_at.is_(None))
            order = (
                [model.created_at, *[getattr(model, n) for n in key_names]]
                if has_created
                else [getattr(model, n) for n in key_names]
            )
            rows = session.scalars(query.order_by(*order).limit(limit + 1).offset(offset)).all()
            items = [models["out"].model_validate(r) for r in rows[:limit]]
        return models["page"](items=items, limit=limit, offset=offset, has_more=len(rows) > limit)

    list_rows.__signature__ = _signature(
        [
            ws_param,
            request_param,
            inspect.Parameter(
                "limit",
                inspect.Parameter.KEYWORD_ONLY,
                annotation=Annotated[int, PAGE_LIMIT],
                default=50,
            ),
            inspect.Parameter(
                "offset",
                inspect.Parameter.KEYWORD_ONLY,
                annotation=Annotated[int, PAGE_OFFSET],
                default=0,
            ),
        ]
    )
    router.add_api_route(
        base,
        list_rows,
        methods=["GET"],
        response_model=models["page"],
        dependencies=[read_guard],
        tags=[label],
    )
    summaries[f"GET /workspaces/{{workspace_id}}{base}"] = (
        f"List {label}",
        "Paginated with `limit` and `offset`.",
    )

    # ---- get one ---------------------------------------------------------------------------
    def get_row(workspace_id, request, **keys):
        with session_for(str(workspace_id)) as session:
            row = lookup(session, workspace_id, keys)
            return models["out"].model_validate(row)

    get_row.__signature__ = _signature([ws_param, request_param, *key_params])
    router.add_api_route(
        f"{base}/{key_path}",
        get_row,
        methods=["GET"],
        response_model=models["out"],
        dependencies=[read_guard],
        tags=[label],
    )
    summaries[f"GET /workspaces/{{workspace_id}}{base}/{key_path}"] = (
        f"Get one {label[:-1] if label.endswith('s') else label}",
        "Returns one row by its key. 404 if it does not exist in this workspace.",
    )

    if table.name not in CRUD_TABLES:
        return summaries

    # ---- create ----------------------------------------------------------------------------
    def create_row(workspace_id, request, body):
        data = body.model_dump(exclude_unset=True)
        if has_actor:
            data["created_by"] = auth.actor(request)
        with session_for(str(workspace_id)) as session:
            try:
                row = model(workspace_id=workspace_id, **data)
                session.add(row)
                session.flush()
            except IntegrityError as exc:
                _error(exc)
            return models["out"].model_validate(row)

    create_row.__signature__ = _signature(
        [
            ws_param,
            request_param,
            inspect.Parameter("body", inspect.Parameter.KEYWORD_ONLY, annotation=models["create"]),
        ]
    )
    router.add_api_route(
        base,
        create_row,
        methods=["POST"],
        status_code=201,
        response_model=models["out"],
        dependencies=[write_guard],
        tags=[label],
    )
    summaries[f"POST /workspaces/{{workspace_id}}{base}"] = (
        f"Create {label}",
        "Missing required fields are a 422.",
    )

    # ---- update ----------------------------------------------------------------------------
    def update_row(workspace_id, request, body, **keys):
        changes = body.model_dump(exclude_unset=True)
        with session_for(str(workspace_id)) as session:
            row = lookup(session, workspace_id, keys)
            for name, value in changes.items():
                setattr(row, name, value)
            if has_editor:
                row.last_edited_by = auth.actor(request)
            try:
                session.flush()
            except IntegrityError as exc:
                _error(exc)
            return models["out"].model_validate(row)

    update_row.__signature__ = _signature(
        [
            ws_param,
            request_param,
            inspect.Parameter("body", inspect.Parameter.KEYWORD_ONLY, annotation=models["update"]),
            *key_params,
        ]
    )
    router.add_api_route(
        f"{base}/{key_path}",
        update_row,
        methods=["PATCH"],
        response_model=models["out"],
        dependencies=[write_guard],
        tags=[label],
    )
    summaries[f"PATCH /workspaces/{{workspace_id}}{base}/{key_path}"] = (
        f"Update {label}",
        "Only the fields you send change.",
    )

    # ---- delete ----------------------------------------------------------------------------
    def delete_row(workspace_id, request, **keys):
        with session_for(str(workspace_id)) as session:
            row = lookup(session, workspace_id, keys)
            if has_deleted:
                row.deleted_at = datetime.now().astimezone()
            else:
                session.delete(row)
            session.flush()
        return Response(status_code=204)

    delete_row.__signature__ = _signature([ws_param, request_param, *key_params])
    router.add_api_route(
        f"{base}/{key_path}",
        delete_row,
        methods=["DELETE"],
        status_code=204,
        dependencies=[write_guard],
        tags=[label],
    )
    summaries[f"DELETE /workspaces/{{workspace_id}}{base}/{key_path}"] = (
        f"Delete {label}",
        "Soft delete: the row is kept and hidden." if has_deleted else "Hard delete. Returns 204.",
    )
    return summaries


def build() -> dict:
    """Register every table's routes once. Returns the summaries for the operations table."""
    summaries = {}
    module = sys.modules[__name__]
    by_name = {t.name: t for t in Base.metadata.sorted_tables}
    for name in sorted(CRUD_TABLES | READ_TABLES):
        table = by_name[name]
        models = _models(table)
        for model in models.values():
            setattr(module, model.__name__, model)
        summaries.update(_register(table, models))
    return summaries


# `collections.schema` shadows a BaseModel method name; pydantic only warns about it.
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message="Field name .* shadows", category=UserWarning)
    SUMMARIES = build()
