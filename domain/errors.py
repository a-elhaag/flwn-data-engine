"""Domain error type, DB-constraint -> error mapping, and exception handlers."""

import logging

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel
from pydantic.alias_generators import to_camel
from sqlalchemy import ForeignKeyConstraint
from sqlalchemy.exc import IntegrityError

import db.models  # noqa: F401  registers every table on Base.metadata
from db.base import Base

logger = logging.getLogger("domain")


class ErrorResponse(BaseModel):
    code: str
    message: str
    field: str | None = None


class DomainError(Exception):
    def __init__(self, status: int, code: str, message: str, field: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.field = field

    def body(self) -> dict:
        return ErrorResponse(code=self.code, message=self.message, field=self.field).model_dump()


def not_found(entity: str) -> DomainError:
    """entity in UPPER_SNAKE, e.g. "WORK_ITEM"."""
    return DomainError(
        404, f"{entity}_NOT_FOUND", f"{entity.replace('_', ' ').capitalize()} not found"
    )


def error_responses(*statuses: int) -> dict:
    """OpenAPI `responses=` entries using the shared ErrorResponse body."""
    return {s: {"model": ErrorResponse} for s in statuses}


class DomainRoute(APIRoute):
    """Marker route class: lets the 422 handler tell domain routes from memory routes."""


# --- constraint -> error table -------------------------------------------------

_CONFLICT = "Conflict with existing data"

# name -> (http status, code, camelCase field)
_CONSTRAINT_ERRORS: dict[str, tuple[int, str, str | None]] = {
    "uq_users_email": (409, "USER_ALREADY_EXISTS", "email"),
    "uq_members_organization_id_user_id": (409, "MEMBER_ALREADY_EXISTS", "userId"),
    "uq_members_organization_id_name_ai": (409, "MEMBER_ALREADY_EXISTS", "name"),
    "uq_members_organization_id_email_ai": (409, "MEMBER_ALREADY_EXISTS", "email"),
    "uq_workspaces_organization_id_name_active": (409, "WORKSPACE_ALREADY_EXISTS", "name"),
    "uq_teams_workspace_id_name_active": (409, "TEAM_ALREADY_EXISTS", "name"),
    "uq_projects_workspace_id_name_active": (409, "PROJECT_ALREADY_EXISTS", "name"),
    "uq_projects_organization_id_key": (409, "PROJECT_KEY_ALREADY_EXISTS", "key"),
    "uq_sprints_project_id_team_id_name": (409, "SPRINT_ALREADY_EXISTS", "name"),
    "uq_sprints_project_id_team_id_active": (409, "SPRINT_ALREADY_ACTIVE", "status"),
    "pk_team_members": (409, "TEAM_MEMBER_ALREADY_EXISTS", "memberId"),
    "pk_project_teams": (409, "PROJECT_TEAM_ALREADY_EXISTS", "teamId"),
    "ck_tasks_done_completed": (422, "VALIDATION_ERROR", "completedAt"),
    "ck_sprints_dates": (422, "VALIDATION_ERROR", "endDate"),
    "ck_members_human_or_ai": (422, "VALIDATION_ERROR", "type"),
    # Not in the spec table. Task numbers come from projects.task_counter, so a clash
    # is only possible if the counter was tampered with.
    "uq_tasks_project_id_number": (409, "TASK_NUMBER_CONFLICT", "number"),
}

# Ids are DB-generated and every (id, organization_id) / (id, project_id) unique is
# only a composite-FK target, so none of these can be hit through the API. Mapped
# defensively so they never surface as a 500.
_INTERNAL_CONFLICTS = frozenset(
    {
        "pk_users", "pk_organizations", "pk_members", "pk_workspaces", "pk_teams",
        "pk_projects", "pk_work_items", "pk_sprints", "pk_tasks",
        "uq_members_id_organization_id", "uq_workspaces_id_organization_id",
        "uq_teams_id_organization_id", "uq_projects_id_organization_id",
        "uq_work_items_id_project_id", "uq_sprints_id_project_id",
    }
)  # fmt: skip

_PROJECT_TEAM_HAS_SPRINTS = "fk_sprints_project_id_team_id_project_teams"


def _fk_fields() -> dict[str, str]:
    """FK constraint name -> camelCase name of its first local column."""
    fields = {}
    for table in Base.metadata.tables.values():
        for constraint in table.constraints:
            if isinstance(constraint, ForeignKeyConstraint) and constraint.name:
                fields[str(constraint.name)] = to_camel(constraint.column_keys[0])
    return fields


_FK_FIELDS = _fk_fields()


def map_constraint(name: str | None, is_delete: bool = False) -> DomainError | None:
    """The DomainError for a violated constraint, or None when it is unmapped."""
    if name is None:
        return None
    if name == _PROJECT_TEAM_HAS_SPRINTS and is_delete:
        return DomainError(
            409, "PROJECT_TEAM_HAS_SPRINTS", "Team still has sprints in this project", "teamId"
        )
    if name in _CONSTRAINT_ERRORS:
        status, code, field = _CONSTRAINT_ERRORS[name]
        message = "Invalid value" if status == 422 else "Already exists"
        return DomainError(status, code, message, field)
    if name in _INTERNAL_CONFLICTS:
        return DomainError(409, "CONFLICT", _CONFLICT)
    if name.startswith("fk_"):
        field = _FK_FIELDS.get(name)
        if field is None:
            return None
        return DomainError(422, "INVALID_REFERENCE", "Referenced row does not exist here", field)
    if name.startswith("ck_"):
        return DomainError(422, "VALIDATION_ERROR", "Value violates a data rule")
    return None


# --- handlers ------------------------------------------------------------------


def _render(error: DomainError) -> JSONResponse:
    return JSONResponse(status_code=error.status, content=error.body())


async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    return _render(exc)


async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    is_delete = (exc.statement or "").lstrip().upper().startswith("DELETE")
    error = map_constraint(name, is_delete)
    if error is None:
        logger.error("Unmapped integrity error (constraint=%s): %s", name, exc.orig)
        error = DomainError(500, "INTERNAL_ERROR", "Internal error")
    return _render(error)


async def validation_error_handler(request: Request, exc: RequestValidationError):
    if not isinstance(request.scope.get("route"), DomainRoute):
        return await request_validation_exception_handler(request, exc)
    first = exc.errors()[0]
    parts = [str(p) for p in first["loc"][1:]]  # drop "body" / "query" / "path" / "header"
    field = ".".join(parts) or None
    return _render(DomainError(422, "VALIDATION_ERROR", first["msg"], field))


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, domain_error_handler)
    app.add_exception_handler(IntegrityError, integrity_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
