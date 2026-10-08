"""Make uploaded files searchable: extract text, chunk it, embed it, store the chunks.

The `files` table is the work queue. A file waiting to be indexed has index_status 'pending';
workers claim one at a time with FOR UPDATE SKIP LOCKED, so any number of workers and replicas
can run without doing the same file twice, and a restart loses nothing. A claim that has sat in
'indexing' too long (a crashed worker) is taken over.

Chat and meeting files are never indexed: they can belong to a private channel or meeting, and
search results would show them to the whole workspace.
"""

import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, delete, or_, select

from app.clients import inference
from app.config import settings
from app.db import events
from app.db import session as db
from app.db.models.files import File
from app.db.models.memory import Chunk
from app.db.session import session_for, workspace_session
from app.storage import extract
from app.storage.blobs import BlobStorage
from app.storage.chunking import chunk_sections
from app.storage.errors import Unindexable

logger = logging.getLogger(__name__)
INDEXABLE_SOURCES = {"workspace", "agent", "import"}


def plan(source: str, content_type: str | None, name: str) -> tuple[str, str | None]:
    """What happens to a newly verified file: ('pending', None) or ('skipped', why)."""
    if source not in INDEXABLE_SOURCES:
        return "skipped", "chat and meeting files are not indexed (they may be private)"
    if extract.file_type(content_type, name) is None:
        return "skipped", f"no text extractor for {content_type or 'this file type'}"
    return "pending", None


@dataclass(frozen=True)
class Claim:
    file_id: uuid.UUID
    workspace_id: str
    container: str
    blob_path: str
    name: str
    content_type: str | None
    team_id: uuid.UUID | None
    project_id: uuid.UUID | None


def claim_next() -> Claim | None:
    """Take the oldest waiting file, or None. Safe to call from many workers at once."""
    stuck_before = datetime.now(UTC) - timedelta(minutes=settings.INDEX_STUCK_MINUTES)
    waiting = or_(
        File.index_status == "pending",
        and_(File.index_status == "indexing", File.index_started_at < stuck_before),
    )
    with workspace_session(db.engine(), "", service=True) as session:  # spans workspaces
        row = session.scalars(
            select(File)
            .where(waiting, File.deleted_at.is_(None), File.status == "ready")
            .order_by(File.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).first()
        if row is None:
            return None
        row.index_status, row.index_started_at = "indexing", datetime.now(UTC)
        return Claim(
            row.id,
            str(row.workspace_id),
            row.container,
            row.blob_path,
            row.name,
            row.content_type,
            row.team_id,
            row.project_id,
        )


def _finish(
    claim: Claim, status: str, error: str | None = None, pieces=None, vectors=None, model=None
):
    """Record the outcome in one transaction: replace the file's chunks and set its status."""
    with session_for(claim.workspace_id) as session:
        row = session.scalars(
            select(File).where(
                File.workspace_id == uuid.UUID(claim.workspace_id), File.id == claim.file_id
            )
        ).one_or_none()
        if row is None or row.deleted_at is not None:
            return  # deleted while it was being read: write nothing
        session.execute(
            delete(Chunk).where(Chunk.source_type == "file", Chunk.source_id == claim.file_id)
        )
        for index, (piece, vector) in enumerate(zip(pieces or [], vectors or [], strict=True)):
            session.add(
                Chunk(
                    workspace_id=uuid.UUID(claim.workspace_id),
                    source_type="file",
                    source_id=claim.file_id,
                    chunk_index=index,
                    text=piece.text,
                    heading_path=piece.heading_path,
                    page=piece.page,
                    start_offset=piece.start,
                    end_offset=piece.end,
                    team_id=claim.team_id,
                    project_id=claim.project_id,
                    content_hash=piece.content_hash,
                    embedding=vector,
                    embedding_model=model,
                )
            )
        row.index_status, row.index_error = status, error
        row.chunk_count = len(pieces or [])
        row.indexed_at = datetime.now(UTC) if status == "done" else None
        events.record(
            session,
            claim.workspace_id,
            None,
            "file",
            claim.file_id,
            "indexed" if status == "done" else f"index_{status}",
            {"chunks": row.chunk_count, "error": error},
        )


def process(claim: Claim, storage: BlobStorage) -> None:
    """Index one claimed file. Never raises: every outcome is written to the file's row."""
    try:
        data = storage.download(claim.container, claim.blob_path, settings.INDEX_MAX_BYTES)
        extracted = extract.extract(data, claim.content_type, claim.name)
        pieces, cut = chunk_sections(extracted.sections, settings.INDEX_MAX_CHUNKS)
        if not pieces:
            raise Unindexable("no text found")
        vectors = inference.embed_many([piece.embedding_text for piece in pieces])
        notes = list(extracted.notes)
        if cut:
            notes.append(f"indexed the first {len(pieces)} sections only")
        _finish(
            claim, "done", "; ".join(notes) or None, pieces, vectors, settings.EMBEDDING_DEPLOYMENT
        )
        logger.info("indexed file %s: %d chunks", claim.file_id, len(pieces))
    except Unindexable as exc:
        _finish(claim, "skipped", str(exc))
    except Exception as exc:  # a failure is recorded, and retried only on request
        logger.exception("indexing file %s failed", claim.file_id)
        _finish(claim, "failed", f"{type(exc).__name__}: {exc}"[:500])


def run_once(storage: BlobStorage) -> bool:
    """Index one waiting file. False if nothing was waiting."""
    claim = claim_next()
    if claim is None:
        return False
    process(claim, storage)
    return True


class IndexWorker(threading.Thread):
    """Background loop: index waiting files, sleep when there are none, wake early on a signal."""

    def __init__(self, storage: BlobStorage):
        super().__init__(name="file-indexer", daemon=True)
        self.storage = storage
        self._stop_event = threading.Event()
        self._wake = threading.Event()

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                busy = run_once(self.storage)
            except Exception:
                logger.exception("indexer loop error")
                busy = False
            if not busy:
                self._wake.wait(settings.INDEX_POLL_SECONDS)
                self._wake.clear()

    def wake(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake.set()


_worker: IndexWorker | None = None


def start_worker(storage: BlobStorage) -> IndexWorker:
    global _worker
    _worker = IndexWorker(storage)
    _worker.start()
    return _worker


def stop_worker() -> None:
    global _worker
    if _worker is not None:
        _worker.stop()
        _worker = None


def wake() -> None:
    """Tell the local worker (if any) there is new work; other replicas find it by polling."""
    if _worker is not None:
        _worker.wake()
