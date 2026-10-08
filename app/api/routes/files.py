"""Files: register an upload, finish it, list, get a download link, delete."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response

from app.api.auth import Principal
from app.api.deps import FILES_DELETE, FILES_READ, FILES_WRITE, Files
from app.api.schemas import FileKind, StartUploadRequest
from app.storage.files import DownloadLink, FilePage, FileRecord, UploadTicket

router = APIRouter(prefix="/workspaces/{workspace_id}/files")


@router.post("", status_code=201)
def start_upload(
    request: StartUploadRequest, files: Files, principal: Annotated[Principal, FILES_WRITE]
) -> UploadTicket:
    """Returns a short-lived URL. PUT the bytes to it with the returned headers, then call
    `complete`. Meeting recordings can only be registered by the trusted backend (service key),
    because they must come from a meeting where everyone consented."""
    if (request.kind == "recording" or request.source == "meeting") and not principal.service:
        raise HTTPException(status_code=403, detail="Recordings are created by the meeting service")
    return files.start_upload(**request.model_dump())


@router.post("/{file_id}/complete", dependencies=[FILES_WRITE])
def complete_upload(file_id: UUID, files: Files) -> FileRecord:
    return files.complete(str(file_id))


@router.get("", dependencies=[FILES_READ])
def list_files(
    files: Files,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    kind: FileKind | None = None,
    project_id: UUID | None = None,
    folder_id: UUID | None = None,
) -> FilePage:
    return files.list(limit, offset, kind, project_id, folder_id)


@router.get("/{file_id}", dependencies=[FILES_READ])
def get_file(file_id: UUID, files: Files) -> FileRecord:
    return files.get(str(file_id))


@router.get("/{file_id}/download", dependencies=[FILES_READ])
def download_file(file_id: UUID, files: Files) -> DownloadLink:
    return files.download_link(str(file_id))


@router.delete("/{file_id}", status_code=204, dependencies=[FILES_DELETE])
def delete_file(file_id: UUID, files: Files) -> Response:
    files.delete(str(file_id))
    return Response(status_code=204)
