"""File registry: rows in the `files` table describe blobs in Azure Storage.

An upload takes two calls so the bytes never pass through the API. `start_upload` creates the
row and a short-lived upload link; the client sends the bytes straight to Azure; `complete`
checks the blob really arrived and is not too large, then marks the file ready.
"""

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import psycopg
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db.models.files import File
from app.db.models.identity import Workspace
from app.db.session import session_for
from app.memory.errors import WorkspaceNotFound
from app.storage import blobs
from app.storage.blobs import BlobStorage
from app.storage.errors import FileNotFound, InvalidReference, UploadIncomplete, UploadRejected

MB = 1024 * 1024
# Single-request uploads over 256 MiB need this service version or later (limit 5000 MiB).
STORAGE_API_VERSION = "2023-11-03"
DEFAULT_LIMIT = 100 * MB
# Largest accepted size per kind. SAS uploads cannot enforce a size, so `complete` checks it.
LIMITS = {
    "report": 5 * MB,
    "image": 25 * MB,
    "voice_note": 25 * MB,
    "pdf": 100 * MB,
    "document": 100 * MB,
    "audio": 500 * MB,
    "video": 2000 * MB,
    "recording": 4000 * MB,
}


def container_for(kind: str, source: str) -> str:
    if kind == "recording" or source == "meeting":
        return blobs.MEETING_RECORDINGS
    if kind == "report" or source == "agent":
        return blobs.AGENT_REPORTS
    if source == "chat":
        return blobs.CHAT_MEDIA
    return blobs.WORKSPACE_FILES


def safe_name(name: str) -> str:
    """A file name safe to use inside a blob path: no directories, no odd characters."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    return re.sub(r"[^\w.\- ]", "_", base).strip(" .")[:120] or "file"


@dataclass
class UploadTicket:
    file_id: str
    upload_url: str
    method: str
    headers: dict[str, str]
    expires_at: datetime


@dataclass
class FileRecord:
    id: str
    kind: str
    source: str
    name: str
    content_type: str | None
    size_bytes: int | None
    status: str
    project_id: str | None
    folder_id: str | None
    team_id: str | None
    created_at: datetime
    duration_ms: int | None = None
    error: str | None = None


@dataclass
class DownloadLink:
    url: str
    expires_at: datetime


@dataclass
class FilePage:
    items: list[FileRecord] = field(default_factory=list)


def _record(row: File) -> FileRecord:
    text = lambda value: str(value) if value else None  # noqa: E731
    return FileRecord(
        id=str(row.id),
        kind=row.kind,
        source=row.source,
        name=row.name,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        status=row.status,
        project_id=text(row.project_id),
        folder_id=text(row.folder_id),
        team_id=text(row.team_id),
        created_at=row.created_at,
        duration_ms=row.duration_ms,
        error=row.error,
    )


class FileService:
    def __init__(self, workspace_id: str, storage: BlobStorage):
        self.workspace_id = str(uuid.UUID(workspace_id))
        self.storage = storage

    def _row(self, session, file_id: str | uuid.UUID) -> File:
        row = session.scalars(
            select(File).where(
                File.workspace_id == uuid.UUID(self.workspace_id),
                File.id == uuid.UUID(str(file_id)),
                File.deleted_at.is_(None),
            )
        ).one_or_none()
        if row is None:
            raise FileNotFound(str(file_id))
        return row

    def start_upload(
        self,
        *,
        kind: str,
        name: str,
        content_type: str,
        size_bytes: int,
        source: str = "workspace",
        folder_id: uuid.UUID | None = None,
        team_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
    ) -> UploadTicket:
        limit = LIMITS.get(kind, DEFAULT_LIMIT)
        if size_bytes > limit:
            raise UploadRejected(f"{kind} files can be at most {limit // MB} MB")
        file_id = uuid.uuid4()
        container = container_for(kind, source)
        path = f"{self.workspace_id}/{file_id}/{safe_name(name)}"
        with session_for(self.workspace_id) as session:
            if session.get(Workspace, uuid.UUID(self.workspace_id)) is None:
                raise WorkspaceNotFound(self.workspace_id)
            session.add(
                File(
                    id=file_id,
                    workspace_id=uuid.UUID(self.workspace_id),
                    kind=kind,
                    source=source,
                    name=name,
                    content_type=content_type,
                    size_bytes=size_bytes,
                    container=container,
                    blob_path=path,
                    folder_id=folder_id,
                    team_id=team_id,
                    project_id=project_id,
                )
            )
            try:
                session.flush()
            except IntegrityError as exc:
                if isinstance(exc.orig, psycopg.errors.ForeignKeyViolation):
                    raise InvalidReference(
                        "folder, team or project not found in this workspace"
                    ) from None
                raise
        ttl = settings.UPLOAD_URL_TTL_SECONDS
        return UploadTicket(
            file_id=str(file_id),
            upload_url=self.storage.upload_url(container, path, ttl),
            method="PUT",
            headers={
                "x-ms-blob-type": "BlockBlob",
                "x-ms-version": STORAGE_API_VERSION,
                "Content-Type": content_type,
            },
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl),
        )

    def complete(self, file_id: str) -> FileRecord:
        """Verify the blob arrived and fits, then mark the file ready. Safe to call twice."""
        problem = None
        with session_for(self.workspace_id) as session:
            row = self._row(session, file_id)
            if row.status == "ready":
                return _record(row)
            # The upload link cannot overwrite, so the bytes read here are the bytes served.
            info = self.storage.info(row.container, row.blob_path)
            if info is None:
                raise UploadIncomplete("the file has not been uploaded yet")
            limit = LIMITS.get(row.kind, DEFAULT_LIMIT)
            if info.size > limit:
                problem = f"{row.kind} files can be at most {limit // MB} MB"
            elif row.size_bytes is not None and info.size != row.size_bytes:
                problem = f"uploaded {info.size} bytes but {row.size_bytes} were announced"
            if problem:
                row.status, row.error = "failed", problem
                self.storage.delete(row.container, row.blob_path)
            else:
                row.status, row.error = "ready", None
                row.size_bytes = info.size
                row.content_type = info.content_type or row.content_type
            record = _record(row)
        if problem:
            raise UploadRejected(problem)
        return record

    def get(self, file_id: str) -> FileRecord:
        with session_for(self.workspace_id) as session:
            return _record(self._row(session, file_id))

    def list(
        self,
        limit: int = 50,
        offset: int = 0,
        kind: str | None = None,
        project_id: uuid.UUID | None = None,
        folder_id: uuid.UUID | None = None,
    ) -> FilePage:
        query = (
            select(File)
            .where(File.workspace_id == uuid.UUID(self.workspace_id), File.deleted_at.is_(None))
            .order_by(File.created_at.desc(), File.id)
            .limit(limit)
            .offset(offset)
        )
        for column, value in (
            (File.kind, kind),
            (File.project_id, project_id),
            (File.folder_id, folder_id),
        ):
            if value is not None:
                query = query.where(column == value)
        with session_for(self.workspace_id) as session:
            return FilePage(items=[_record(row) for row in session.scalars(query)])

    def download_link(self, file_id: str) -> DownloadLink:
        with session_for(self.workspace_id) as session:
            row = self._row(session, file_id)
            if row.status != "ready":
                raise UploadIncomplete(f"the file is {row.status}, not ready")
            container, path, name = row.container, row.blob_path, row.name
        ttl = settings.DOWNLOAD_URL_TTL_SECONDS
        return DownloadLink(
            url=self.storage.download_url(container, path, ttl, filename=safe_name(name)),
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl),
        )

    def delete(self, file_id: str) -> None:
        """Remove the bytes first, then hide the row. If storage fails the call fails and the
        file stays visible, so a retry finishes the job; a deleted file never lingers in storage.
        Azure keeps deleted blobs for 7 days."""
        with session_for(self.workspace_id) as session:
            row = self._row(session, file_id)
            self.storage.delete(row.container, row.blob_path)
            row.deleted_at = datetime.now(UTC)
