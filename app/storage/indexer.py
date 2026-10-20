"""Make uploaded files searchable: extract text, chunk it, embed it, store the chunks.

The `files` table is the work queue. A file waiting to be indexed has index_status 'pending';
workers claim one at a time with FOR UPDATE SKIP LOCKED, so any number of workers and replicas
can run without doing the same file twice, and a restart loses nothing. A claim that has sat in
'indexing' too long (a crashed worker) is taken over.

Audio and video are transcribed first (Azure Speech); the transcript is stored and, for files that
may be searched, indexed like any text.

Chat and meeting files are never indexed: they can belong to a private channel or meeting, and
search results would show them to the whole workspace. Their audio is still transcribed, and the
transcript is readable only through the transcript routes.
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
from app.db.models.meetings import Recording
from app.db.models.memory import Chunk
from app.db.session import session_for, workspace_session
from app.meetings import transcripts
from app.storage import extract, media
from app.storage.blobs import BlobStorage
from app.storage.chunking import Section, chunk_sections
from app.storage.errors import Unindexable

logger = logging.getLogger(__name__)
INDEXABLE_SOURCES = {"workspace", "agent", "import"}


def plan(source: str, content_type: str | None, name: str) -> tuple[str, str | None]:
    """What happens to a newly verified file: ('pending', None) or ('skipped', why)."""
    kind = extract.file_type(content_type, name)
    if kind is None:
        return "skipped", f"no text extractor for {content_type or 'this file type'}"
    if kind in ("audio", "video"):
        return "pending", None  # always transcribed; whether it is searchable is decided later
    if source not in INDEXABLE_SOURCES:
        return "skipped", "chat and meeting files are not indexed (they may be private)"
    return "pending", None


@dataclass(frozen=True)
class Claim:
    file_id: uuid.UUID
    workspace_id: str
    container: str
    blob_path: str
    name: str
    content_type: str | None
    source: str
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
            row.source,
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


def _transcribe(claim: Claim, data: bytes, kind: str) -> list[Section]:
    """Speech to text for an audio or video file. Stores the transcript and returns its turns as
    sections; a recording's row (if this file is one) is marked ready, or failed on an error."""
    name, mime = claim.name, claim.content_type or ""
    if kind == "video":
        data, name, mime = media.audio_track(data, name)
    result = inference.transcribe(data, name, mime.split(";")[0].strip() or "audio/wav")
    segments = transcripts.segments_from(result)
    if not segments:
        raise Unindexable("no speech found")
    with session_for(claim.workspace_id) as session:
        workspace = uuid.UUID(claim.workspace_id)
        recording = session.scalars(
            select(Recording).where(
                Recording.workspace_id == workspace, Recording.file_id == claim.file_id
            )
        ).first()
        transcripts.store(
            session,
            workspace,
            segments=segments,
            language=result.language,
            model="azure-speech-fast",
            duration_ms=result.duration_ms,
            file_id=claim.file_id,
            meeting_id=recording.meeting_id if recording else None,
        )
        if recording:
            recording.status = "ready"
            recording.duration_ms = result.duration_ms
    return [Section(f"{who}: {text}" if who else text) for who, text in transcripts.turns(segments)]


def _mark_recording_failed(claim: Claim) -> None:
    with session_for(claim.workspace_id) as session:
        recording = session.scalars(
            select(Recording).where(
                Recording.workspace_id == uuid.UUID(claim.workspace_id),
                Recording.file_id == claim.file_id,
            )
        ).first()
        if recording and recording.status != "ready":
            recording.status = "failed"


def process(claim: Claim, storage: BlobStorage) -> None:
    """Index one claimed file. Never raises: every outcome is written to the file's row."""
    kind = extract.file_type(claim.content_type, claim.name)
    spoken = kind in ("audio", "video")
    try:
        data = storage.download(
            claim.container,
            claim.blob_path,
            settings.TRANSCRIBE_MAX_BYTES if spoken else settings.INDEX_MAX_BYTES,
        )
        if spoken:
            sections, notes = _transcribe(claim, data, kind), []
            if claim.source not in INDEXABLE_SOURCES:
                _finish(
                    claim,
                    "skipped",
                    "transcribed; not searchable (chat or meeting audio may be private)",
                )
                return
        else:
            extracted = extract.extract(data, claim.content_type, claim.name)
            sections, notes = extracted.sections, list(extracted.notes)
        pieces, cut = chunk_sections(sections, settings.INDEX_MAX_CHUNKS)
        if not pieces:
            raise Unindexable("no text found")
        vectors = inference.embed_many([piece.embedding_text for piece in pieces])
        if cut:
            notes.append(f"indexed the first {len(pieces)} sections only")
        _finish(
            claim, "done", "; ".join(notes) or None, pieces, vectors, settings.EMBEDDING_DEPLOYMENT
        )
        logger.info("indexed file %s: %d chunks", claim.file_id, len(pieces))
    except Unindexable as exc:
        if spoken:
            _mark_recording_failed(claim)
        _finish(claim, "skipped", str(exc))
    except Exception as exc:  # a failure is recorded, and retried only on request
        logger.exception("indexing file %s failed", claim.file_id)
        if spoken:
            _mark_recording_failed(claim)
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
