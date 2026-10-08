"""Memory extraction, persistence, revision, and maintenance for one workspace.

Database transactions stay short: model calls (compression, embeddings, sweep and organize
judgements) run outside any transaction, and results are written back in a fresh one.
"""

import logging
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field

from app.clients import inference
from app.config import settings
from app.db.models.memory import Memory
from app.db.session import advisory_lock, session_for
from app.memory import prompts, vectorizer
from app.memory.errors import ConfirmationRequired, MaintenanceBusy, MemoryNotFound
from app.memory.recall import RankedResult
from app.memory.recall import recall as run_recall
from app.memory.store import MemoryStore, stored_at

logger = logging.getLogger(__name__)
DAY = 24 * 60 * 60
PAGE = 256
IMPORTANT = 4  # importance at or above this is never sent to the sweep LLM
NEIGHBORS = 6  # how many near memories organize compares at once


@dataclass
class MemoryRecord:
    id: str
    text: str
    source: str
    agent: str
    timestamp: float
    last_recalled_at: float | None
    recall_count: int
    importance: int
    pinned: bool
    status: str
    superseded_by: str | None = None
    raw_text: str | None = None


@dataclass
class StoreResult:
    point_id: str
    deduplicated: bool = False


@dataclass
class BrowsePage:
    items: list[MemoryRecord]
    next_cursor: str | None


@dataclass
class SweepResult:
    deleted: int
    scanned: int
    kept: int
    superseded_purged: int
    dry_run: bool


@dataclass
class OrganizeResult:
    clusters: int
    superseded: int
    merged: int
    dry_run: bool


@dataclass
class Stats:
    total: int
    active: int
    superseded: int
    pinned: int
    never_recalled: int
    by_source: dict[str, int] = field(default_factory=dict)


@dataclass
class Neighbor:
    """A memory in an organize group, copied out of the database session."""

    id: uuid.UUID
    text: str
    stored_at: float
    pinned: bool
    importance: int


def _record(row: Memory, with_raw: bool = False) -> MemoryRecord:
    return MemoryRecord(
        id=str(row.id),
        text=row.text,
        source=row.source_type or "",
        agent=row.agent_name or "",
        timestamp=stored_at(row),
        last_recalled_at=row.last_recalled_at.timestamp() if row.last_recalled_at else None,
        recall_count=row.recall_count,
        importance=row.importance,
        pinned=row.pinned,
        status=row.status,
        superseded_by=str(row.superseded_by) if row.superseded_by else None,
        raw_text=row.raw_text if with_raw else None,
    )


class MemorySteward:
    def __init__(self, workspace_id: str):
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise TypeError("workspace_id is required")
        self.workspace_id = str(uuid.UUID(workspace_id))  # ValueError if it is not a UUID

    @contextmanager
    def _store(self):
        with session_for(self.workspace_id) as session:
            yield MemoryStore(session, self.workspace_id)

    # -- write ---------------------------------------------------------------

    def remember(self, text: str, source: str, agent: str) -> str:
        return self.remember_detailed(text, source, agent).point_id

    def remember_detailed(self, text: str, source: str, agent: str) -> StoreResult:
        prepared = vectorizer.prepare(text)
        vector = vectorizer.embed_one(prepared.fact)
        with self._store() as store:
            return self._save(store, prepared, vector, source, agent)

    def ingest(self, items: list[dict]) -> list[StoreResult]:
        """Store many memories; embeds all facts in batched calls."""
        prepared = [vectorizer.prepare(item["text"]) for item in items]
        vectors = vectorizer.embed_many([entry.fact for entry in prepared])
        with self._store() as store:
            return [
                self._save(store, entry, vector, item["source"], item["agent"])
                for entry, vector, item in zip(prepared, vectors, items, strict=False)
            ]

    def _save(self, store: MemoryStore, prepared, vector, source: str, agent: str) -> StoreResult:
        now = time.time()
        threshold = settings.MEMORY_DEDUP_THRESHOLD
        if threshold <= 1.0:
            hits = store.search(vector, 1)
            if hits and hits[0][1] >= threshold:
                existing = hits[0][0]
                store.refresh(existing, now, prepared.importance)
                logger.info(
                    "remember: workspace=%s duplicate of memory=%s", self.workspace_id, existing.id
                )
                return StoreResult(str(existing.id), deduplicated=True)
        row = store.insert(
            text=prepared.fact,
            raw_text=prepared.raw_text,
            source=source,
            agent=agent,
            importance=prepared.importance,
            embedding=vector,
            embedding_model=settings.EMBEDDING_DEPLOYMENT,
            now=now,
        )
        logger.info("remember: workspace=%s stored memory=%s", self.workspace_id, row.id)
        return StoreResult(str(row.id))

    def revise(self, point_id: str, text: str) -> MemoryRecord:
        """Replace a memory's text, keeping its id, history, and recall count."""
        with self._store() as store:
            if store.get(point_id) is None:
                raise MemoryNotFound(point_id)
        fact = text.strip()
        vector = vectorizer.embed_one(fact)
        with self._store() as store:
            row = store.get(point_id, with_raw=True)
            if row is None:
                raise MemoryNotFound(point_id)
            store.replace(row, fact, vector, settings.EMBEDDING_DEPLOYMENT)
            return _record(row, with_raw=True)

    def anchor(self, point_id: str, pinned: bool = True) -> MemoryRecord:
        with self._store() as store:
            row = store.get(point_id, with_raw=True)
            if row is None:
                raise MemoryNotFound(point_id)
            row.pinned = pinned
            return _record(row, with_raw=True)

    # -- read ----------------------------------------------------------------

    def recall(
        self, query: str, agent: str, limit: int = 5, sources: list[str] | None = None
    ) -> list[RankedResult]:
        return run_recall(self.workspace_id, query, agent, limit, sources)

    def open(self, point_id: str) -> MemoryRecord:
        with self._store() as store:
            row = store.get(point_id, with_raw=True)
            if row is None:
                raise MemoryNotFound(point_id)
            return _record(row, with_raw=True)

    def browse(
        self,
        limit: int = 20,
        cursor: str | None = None,
        source: str | None = None,
        agent: str | None = None,
        include_superseded: bool = False,
    ) -> BrowsePage:
        if cursor is not None:
            cursor = str(uuid.UUID(cursor))  # ValueError on garbage
        with self._store() as store:
            rows, next_cursor = store.page(
                limit, cursor, source=source, agent=agent, include_superseded=include_superseded
            )
            return BrowsePage(items=[_record(row) for row in rows], next_cursor=next_cursor)

    def stats(self) -> Stats:
        with self._store() as store:
            data = store.stats()
        return Stats(
            total=data["total"],
            active=data["total"] - data["superseded"],
            superseded=data["superseded"],
            pinned=data["pinned"],
            never_recalled=data["never_recalled"],
            by_source=data["by_source"],
        )

    # -- delete --------------------------------------------------------------

    def forget(self, point_id: str) -> None:
        with self._store() as store:
            store.delete([uuid.UUID(point_id)])

    def purge(self, confirm: str) -> int:
        """Delete every memory in the workspace. `confirm` must equal the workspace id."""
        if confirm != self.workspace_id:
            raise ConfirmationRequired("confirm must equal the workspace id")
        with self._store() as store:
            deleted = store.delete_all()
        logger.warning("purge: workspace=%s deleted=%d", self.workspace_id, deleted)
        return deleted

    # -- maintenance ---------------------------------------------------------

    @contextmanager
    def _exclusive(self):
        """One maintenance run per workspace across all processes and replicas."""
        with advisory_lock(f"memory-maintenance:{self.workspace_id}") as acquired:
            if not acquired:
                raise MaintenanceBusy(self.workspace_id)
            yield

    def run_cleanup(self, retention_days: int = 30) -> int:
        return self.sweep(retention_days).deleted

    def on_sprint_completed(self, retention_days: int = 30) -> int:
        return self.run_cleanup(retention_days=retention_days)

    def sweep(self, retention_days: int = 30, dry_run: bool = False) -> SweepResult:
        """Drop old, never-recalled, unpinned memories the LLM judges irrelevant, and
        purge superseded memories past retention. Unsure or malformed answers keep."""
        with self._exclusive():
            cutoff = time.time() - retention_days * DAY
            with self._store() as store:
                candidates = store.cleanup_candidates(cutoff, settings.SWEEP_SCAN_CAP)
                stale_ids = store.superseded_before(cutoff)
            judged = [row for row in candidates if row.importance < IMPORTANT]
            to_delete: list[uuid.UUID] = []
            for start in range(0, len(judged), settings.SWEEP_BATCH_SIZE):
                batch = judged[start : start + settings.SWEEP_BATCH_SIZE]
                refs = {f"m{index}": row for index, row in enumerate(batch)}
                try:
                    reply = inference.chat(
                        "memory_steward.cleanup_relevance",
                        prompts.sweep_prompt([(ref, row.text) for ref, row in refs.items()]),
                    )
                except Exception as exc:
                    logger.warning("sweep: batch skipped, keeping memories: %s", exc)
                    continue
                decisions = prompts.parse_sweep(reply, list(refs))
                to_delete.extend(row.id for ref, row in refs.items() if not decisions[ref])
            if not dry_run and (to_delete or stale_ids):
                with self._store() as store:
                    # recalled or pinned since selection: leave it alone
                    store.delete(to_delete, only_unused=True)
                    store.delete(stale_ids)
            logger.info(
                "sweep: workspace=%s deleted=%d superseded_purged=%d dry_run=%s",
                self.workspace_id,
                len(to_delete),
                len(stale_ids),
                dry_run,
            )
            return SweepResult(
                deleted=len(to_delete),
                scanned=len(candidates),
                kept=len(candidates) - len(to_delete),
                superseded_purged=len(stale_ids),
                dry_run=dry_run,
            )

    def organize(self, dry_run: bool = False, max_clusters: int = 50) -> OrganizeResult:
        """Find near-duplicate or contradicting memories and soft-supersede the losers.
        Nothing is hard-deleted here; sweep purges superseded memories after retention."""
        with self._exclusive():
            seen: set[uuid.UUID] = set()
            clusters = superseded = merged = scanned = 0
            cursor = None
            while clusters < max_clusters and scanned < settings.ORGANIZE_SCAN_CAP:
                with self._store() as store:
                    rows, cursor = store.page(PAGE, cursor, with_vectors=True)
                    page = [(row.id, list(row.embedding)) for row in rows]
                scanned += len(page)
                for memory_id, vector in page:
                    if clusters >= max_clusters:
                        break
                    if memory_id in seen:
                        continue
                    group = self._neighbors(vector, seen)
                    seen.update(member.id for member in group)
                    seen.add(memory_id)
                    if len(group) < 2:
                        continue
                    outcome = self._organize_group(group, dry_run)
                    if outcome is not None:
                        clusters += 1
                        superseded += outcome[0]
                        merged += outcome[1]
                if cursor is None:
                    break
            logger.info(
                "organize: workspace=%s clusters=%d superseded=%d merged=%d dry_run=%s",
                self.workspace_id,
                clusters,
                superseded,
                merged,
                dry_run,
            )
            return OrganizeResult(clusters, superseded, merged, dry_run)

    def _neighbors(self, vector: list[float], seen: set[uuid.UUID]) -> list[Neighbor]:
        with self._store() as store:
            return [
                Neighbor(row.id, row.text, stored_at(row), row.pinned, row.importance)
                for row, score in store.search(vector, NEIGHBORS)
                if score >= settings.MEMORY_ORGANIZE_THRESHOLD and row.id not in seen
            ]

    def _organize_group(self, group: list[Neighbor], dry_run: bool) -> tuple[int, int] | None:
        ordered = sorted(group, key=lambda member: member.stored_at)
        refs = {f"m{index}": member for index, member in enumerate(ordered)}
        try:
            reply = inference.chat(
                "memory_steward.organize",
                prompts.organize_prompt([(ref, member.text) for ref, member in refs.items()]),
            )
        except Exception as exc:
            logger.warning("organize: group skipped: %s", exc)
            return None
        verdict = prompts.parse_organize(reply, list(refs))
        if verdict["action"] == "distinct":
            return None
        now = time.time()
        if verdict["action"] == "merge":
            losers = [member for member in ordered if not member.pinned]
            if not losers:
                return None
            if not dry_run:
                fact = verdict["text"]
                vector = vectorizer.embed_one(fact)
                with self._store() as store:
                    merged = store.insert(
                        text=fact,
                        raw_text=None,
                        source="organize",
                        agent="memory_steward",
                        importance=max(member.importance for member in ordered),
                        embedding=vector,
                        embedding_model=settings.EMBEDDING_DEPLOYMENT,
                        now=now,
                    )
                    store.supersede([member.id for member in losers], merged.id, now)
            return len(losers), 1
        keeper = refs[verdict["keep"]]
        losers = [m for m in ordered if m.id != keeper.id and not m.pinned]
        if not losers:
            return None
        if not dry_run:
            with self._store() as store:
                store.supersede([member.id for member in losers], keeper.id, now)
        return len(losers), 0
