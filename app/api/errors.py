"""Map domain errors to HTTP responses once, so routes stay free of try/except."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.memory.errors import (
    ConfirmationRequired,
    MaintenanceBusy,
    MemoryNotFound,
    WorkspaceNotFound,
)
from app.storage.errors import (
    FileNotFound,
    InvalidReference,
    StorageNotConfigured,
    UploadIncomplete,
    UploadRejected,
)


def _reply(status: int, detail: str | None = None):
    """A fixed message, or the error's own message when `detail` is None."""

    async def handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": detail or str(exc)}, status_code=status)

    return handler


def register(app: FastAPI) -> None:
    app.add_exception_handler(MemoryNotFound, _reply(404, "Memory not found"))
    app.add_exception_handler(WorkspaceNotFound, _reply(404, "Workspace not found"))
    app.add_exception_handler(MaintenanceBusy, _reply(409, "Maintenance already running"))
    app.add_exception_handler(
        ConfirmationRequired, _reply(400, "confirm query parameter must equal the workspace id")
    )
    app.add_exception_handler(FileNotFound, _reply(404, "File not found"))
    app.add_exception_handler(StorageNotConfigured, _reply(503, "File storage is not configured"))
    app.add_exception_handler(UploadIncomplete, _reply(409))
    app.add_exception_handler(UploadRejected, _reply(422))
    app.add_exception_handler(InvalidReference, _reply(422))
