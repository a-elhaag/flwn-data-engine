"""Workspace cloud storage. Bytes live in Azure Blob; these rows are the index.

Blob path convention: {workspace_id}/{area}/.../{file_id}/{name}. The container picks the area
(workspace-files, chat-media, meeting-recordings, research-reports).
"""

import uuid
from datetime import datetime

from sqlalchemy import ARRAY, BigInteger, CheckConstraint, Index, SmallInteger, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    EMPTY_JSON,
    ZERO_UUID,
    Base,
    Created,
    IdPk,
    SoftDelete,
    Stamps,
    Tenant,
    one_of,
    tenant_unique,
    tfk,
)

FILE_KINDS = (
    "document",
    "pdf",
    "image",
    "voice_note",
    "audio",
    "video",
    "recording",
    "report",
    "other",
)


class Folder(IdPk, Tenant, Stamps, SoftDelete, Base):
    __tablename__ = "folders"

    team_id: Mapped[uuid.UUID | None]
    project_id: Mapped[uuid.UUID | None]
    parent_folder_id: Mapped[uuid.UUID | None]
    name: Mapped[str]
    created_by: Mapped[uuid.UUID | None]

    __table_args__ = (
        tenant_unique(),
        tfk("team_id", "teams", "set null"),
        tfk("project_id", "projects", "cascade"),
        tfk("parent_folder_id", "folders", "cascade"),
        tfk("created_by", "members", "set null"),
    )


Index(
    "uq_folders_name",
    Folder.workspace_id,
    func.coalesce(Folder.parent_folder_id, text(f"'{ZERO_UUID}'::uuid")),
    func.lower(Folder.name),
    unique=True,
    postgresql_where=text("deleted_at is null"),
)


class File(IdPk, Tenant, Stamps, SoftDelete, Base):
    __tablename__ = "files"

    folder_id: Mapped[uuid.UUID | None]
    team_id: Mapped[uuid.UUID | None]
    project_id: Mapped[uuid.UUID | None]
    uploaded_by: Mapped[uuid.UUID | None]
    kind: Mapped[str]
    source: Mapped[str] = mapped_column(server_default="workspace")
    name: Mapped[str]
    content_type: Mapped[str | None]
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None]
    container: Mapped[str]
    blob_path: Mapped[str]
    duration_ms: Mapped[int | None]  # audio, video, voice notes
    width: Mapped[int | None]
    height: Mapped[int | None]
    waveform: Mapped[list[int] | None] = mapped_column(ARRAY(SmallInteger))  # ~64 peaks
    status: Mapped[str] = mapped_column(server_default="uploading")
    error: Mapped[str | None]
    # Search indexing: none -> pending -> indexing -> done | failed | skipped. The `pending` rows
    # are the work queue; workers claim them with FOR UPDATE SKIP LOCKED.
    index_status: Mapped[str] = mapped_column(server_default="none")
    index_error: Mapped[str | None]
    index_started_at: Mapped[datetime | None]
    indexed_at: Mapped[datetime | None]
    chunk_count: Mapped[int] = mapped_column(server_default="0")
    metadata_: Mapped[dict] = mapped_column("metadata", server_default=EMPTY_JSON)

    __table_args__ = (
        tenant_unique(),
        one_of("kind", *FILE_KINDS),
        one_of("source", "workspace", "chat", "meeting", "agent", "import"),
        one_of("status", "uploading", "scanning", "processing", "ready", "failed", "quarantined"),
        one_of("index_status", "none", "pending", "indexing", "done", "failed", "skipped"),
        # a file's blob must live under its own workspace's prefix
        CheckConstraint(
            "position((workspace_id::text || '/') in blob_path) = 1", name="blob_in_workspace"
        ),
        tfk("folder_id", "folders", "set null"),
        tfk("team_id", "teams", "set null"),
        tfk("project_id", "projects", "set null"),
        tfk("uploaded_by", "members", "set null"),
        Index("uq_files_container_blob_path", "container", "blob_path", unique=True),
        Index("ix_files_workspace_id_kind_created_at", "workspace_id", "kind", "created_at"),
        Index(
            "ix_files_index_queue",
            "index_status",
            "index_started_at",
            postgresql_where=text("index_status in ('pending', 'indexing')"),
        ),
        Index("ix_files_folder_id", "folder_id", postgresql_where=text("folder_id is not null")),
        Index("ix_files_project_id", "project_id", postgresql_where=text("project_id is not null")),
        Index(
            "ix_files_sha256", "workspace_id", "sha256", postgresql_where=text("sha256 is not null")
        ),
    )


class FileDerivative(IdPk, Tenant, Created, Base):
    """Computed from a file: thumbnails, OCR text, image captions, parsed document text."""

    __tablename__ = "file_derivatives"

    file_id: Mapped[uuid.UUID]
    kind: Mapped[str]
    content: Mapped[str | None]
    container: Mapped[str | None]
    blob_path: Mapped[str | None]
    model: Mapped[str | None]

    __table_args__ = (
        one_of("kind", "thumbnail", "preview", "ocr_text", "caption", "parsed_text"),
        CheckConstraint("content is not null or blob_path is not null", name="has_content"),
        tfk("file_id", "files", "cascade"),
        Index("ix_file_derivatives_file_id_kind", "file_id", "kind"),
    )
