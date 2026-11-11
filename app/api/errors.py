"""Map domain errors to HTTP responses once, so routes stay free of try/except.

Every client error has the same body: {"detail": <message>, "code": <stable code>, "fields": <dict or null>}.
`code` is the contract the backend reads; the list lives in app/domain/error_codes.py.
`detail` is the English text, kept for people and older clients. `fields` names the input that
failed (a body or query field, or a header) with its message, for 422 errors.
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.db.atom_access import AtomAccessError
from app.domain.decisions.errors import ConflictNotFound, ConflictStateError, HumanRequired
from app.domain.error_codes import CODES
from app.domain.meetings.errors import (
    ConsentMissing,
    MeetingForbidden,
    MeetingNotFound,
    MeetingStateError,
)
from app.domain.memory.errors import (
    ConfirmationRequired,
    MaintenanceBusy,
    MemberNotFound,
    MemoryNotFound,
    WorkspaceNotFound,
)
from app.domain.storage.errors import (
    FileNotFound,
    InvalidReference,
    StorageNotConfigured,
    UploadIncomplete,
    UploadRejected,
)

# Codes for errors FastAPI and Starlette raise themselves (no code of our own).
HTTP_CODES = {
    401: "unauthorized",
    403: "forbidden",
    404: "route_not_found",
    405: "method_not_allowed",
}


class ApiError(HTTPException):
    """An HTTP error with a stable code, and the failing fields for bad input."""

    def __init__(
        self, status_code: int, code: str, detail: str, fields: dict[str, str] | None = None
    ):
        super().__init__(status_code=status_code, detail=detail)
        self.code = code
        self.fields = fields


def api_error(
    status_code: int, code: str, detail: str, fields: dict[str, str] | None = None
) -> ApiError:
    """The one way to raise a client error from a route: status, code, message, fields."""
    return ApiError(status_code, code, detail, fields)


def code_of(exc: Exception, default: str) -> str:
    """The code a domain error carries, or `default` for one that was never given a code."""
    return getattr(exc, "code", None) or default


def _body(detail, code: str, fields: dict[str, str] | None = None) -> dict:
    return {"detail": detail, "code": code, "fields": fields}


def _reply(status: int, detail: str | None = None):
    """A fixed message, or the error's own message when `detail` is None."""

    async def handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            _body(detail or str(exc), exc.code),
            status_code=status,
        )

    return handler


async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    fields: dict[str, str] = {}
    for error in errors:
        loc = error["loc"][1:] or error["loc"]  # drop "body", "query", ...
        fields.setdefault(".".join(str(part) for part in loc), error["msg"])
    # A validator that raises PydanticCustomError with one of our codes names that code.
    code = next((e["type"] for e in errors if e["type"] in CODES), "validation_error")
    return JSONResponse(_body(jsonable_encoder(errors), code, fields), status_code=422)


async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = getattr(exc, "code", None) or HTTP_CODES.get(exc.status_code, "http_error")
    return JSONResponse(
        _body(exc.detail, code, getattr(exc, "fields", None)),
        status_code=exc.status_code,
        headers=getattr(exc, "headers", None),
    )


def register(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, _http)
    app.add_exception_handler(RequestValidationError, _validation)
    app.add_exception_handler(AtomAccessError, _reply(403))
    app.add_exception_handler(MemoryNotFound, _reply(404, "Memory not found"))
    app.add_exception_handler(WorkspaceNotFound, _reply(404, "Workspace not found"))
    app.add_exception_handler(
        MemberNotFound, _reply(422, "Acting member not found in this workspace")
    )
    app.add_exception_handler(MaintenanceBusy, _reply(409, "Maintenance already running"))
    app.add_exception_handler(
        ConfirmationRequired, _reply(400, "confirm query parameter must equal the workspace id")
    )
    app.add_exception_handler(FileNotFound, _reply(404, "File not found"))
    app.add_exception_handler(StorageNotConfigured, _reply(503, "File storage is not configured"))
    app.add_exception_handler(UploadIncomplete, _reply(409))
    app.add_exception_handler(UploadRejected, _reply(422))
    app.add_exception_handler(InvalidReference, _reply(422))
    app.add_exception_handler(MeetingNotFound, _reply(404, "Meeting not found"))
    app.add_exception_handler(MeetingForbidden, _reply(403))
    app.add_exception_handler(ConsentMissing, _reply(409))
    app.add_exception_handler(MeetingStateError, _reply(409))
    app.add_exception_handler(ConflictNotFound, _reply(404, "Conflict not found"))
    app.add_exception_handler(ConflictStateError, _reply(409))
    app.add_exception_handler(HumanRequired, _reply(403))
