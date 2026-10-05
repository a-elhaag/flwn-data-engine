"""Memory extraction, persistence, revision, and maintenance for workspace information."""

import logging
import threading
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from clients import inference, vector_store
from config import settings
from memory import prompts, vectorizer
from memory.errors import ConfirmationRequired, MaintenanceBusy, MemoryNotFound
from retrieval import search
from retrieval.search import RankedResult

logger = logging.getLogger(__name__)
DAY = 24 * 60 * 60
PAGE = 256
IMPORTANT = 4  # importance at or above this is never sent to the sweep LLM

_maintenance_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)


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
    by_source_truncated: bool = False


def _record(point, with_raw: bool = False) -> MemoryRecord:
    payload = point.payload
    return MemoryRecord(
        id=str(point.id),
        text=payload["text"],
        source=payload["source"],
        agent=payload["agent"],
        timestamp=payload["timestamp"],
        last_recalled_at=payload.get("last_recalled_at"),
        recall_count=payload.get("recall_count", 0),
        importance=payload.get("importance", 3),
        pinned=bool(payload.get("pinned", False)),
        status=payload.get("status", "active"),
        superseded_by=payload.get("superseded_by"),
        raw_text=payload.get("raw_text") if with_raw else None,
    )


class MemorySteward:
    def __init__(self, workspace_id: str):
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise TypeError("workspace_id is required")
        self.workspace_id = workspace_id

    # -- write ---------------------------------------------------------------

    def remember(self, text: str, source: str, agent: str) -> str:
        return self.remember_detailed(text, source, agent).point_id

    def remember_detailed(self, text: str, source: str, agent: str) -> StoreResult:
        prepared = vectorizer.prepare(text)
        vector = vectorizer.embed_one(prepared.fact)
        return self._store(prepared, vector, source, agent)

    def ingest(self, items: list[dict]) -> list[StoreResult]:
        """Store many memories; embeds all facts in batched calls."""
        prepared = [vectorizer.prepare(item["text"]) for item in items]
        vectors = vectorizer.embed_many([entry.fact for entry in prepared])
        return [
            self._store(entry, vector, item["source"], item["agent"])
            for entry, vector, item in zip(prepared, vectors, items)
        ]

    def _store(self, prepared, vector, source: str, agent: str) -> StoreResult:
        now = time.time()
        threshold = settings.MEMORY_DEDUP_THRESHOLD
        if threshold <= 1.0:
            hits = vector_store.search(vector, self.workspace_id, limit=1)
            if hits and hits[0].score >= threshold:
                existing = hits[0]
                vector_store.set_payload(
                    [str(existing.id)],
                    self.workspace_id,
                    {
                        "timestamp": now,
                        "importance": max(
                            prepared.importance, existing.payload.get("importance", 3)
                        ),
                    },
                )
                logger.info(
                    "remember: workspace=%s duplicate of point=%s",
                    self.workspace_id,
                    existing.id,
                )
                return StoreResult(str(existing.id), deduplicated=True)
        point_id = vector_store.upsert(
            embedding=vector,
            workspace_id=self.workspace_id,
            text=prepared.fact,
            source=source,
            agent=agent,
            timestamp=now,
            raw_text=prepared.raw_text,
            importance=prepared.importance,
        )
        logger.info(
            "remember: workspace=%s stored point=%s", self.workspace_id, point_id
        )
        return StoreResult(point_id)

    def revise(self, point_id: str, text: str) -> MemoryRecord:
        """Replace a memory's text, keeping its id, history, and recall count."""
        existing = vector_store.get(point_id, self.workspace_id)
        if existing is None:
            raise MemoryNotFound(point_id)
        fact = text.strip()
        payload = {**existing.payload, "text": fact, "revised_at": time.time()}
        vector_store.replace(
            point_id, self.workspace_id, vectorizer.embed_one(fact), payload
        )
        return self.open(point_id)

    def anchor(self, point_id: str, pinned: bool = True) -> MemoryRecord:
        if vector_store.get(point_id, self.workspace_id) is None:
            raise MemoryNotFound(point_id)
        vector_store.set_payload([point_id], self.workspace_id, {"pinned": pinned})
        return self.open(point_id)

    # -- read ----------------------------------------------------------------

    def recall(
        self,
        query: str,
        agent: str,
        limit: int = 5,
        sources: list[str] | None = None,
    ) -> list[RankedResult]:
        return search.recall(self.workspace_id, query, agent, limit, sources)

    def open(self, point_id: str) -> MemoryRecord:
        point = vector_store.get(point_id, self.workspace_id)
        if point is None:
            raise MemoryNotFound(point_id)
        return _record(point, with_raw=True)

    def browse(
        self,
        limit: int = 20,
        cursor: str | None = None,
        source: str | None = None,
        agent: str | None = None,
        include_superseded: bool = False,
    ) -> BrowsePage:
        must = []
        if source:
            must.append(vector_store._field("source", source))
        if agent:
            must.append(vector_store._field("agent", agent))
        if cursor is not None:
            cursor = str(uuid.UUID(cursor))  # ValueError on garbage
        records, next_offset = vector_store.scroll(
            self.workspace_id,
            must=must,
            include_superseded=include_superseded,
            limit=limit,
            offset=cursor,
        )
        return BrowsePage(
            items=[_record(point) for point in records],
            next_cursor=str(next_offset) if next_offset is not None else None,
        )

    def stats(self) -> Stats:
        ws = self.workspace_id
        total = vector_store.count(ws, include_superseded=True)
        superseded = vector_store.count(
            ws, must=[vector_store._field("status", "superseded")], include_superseded=True
        )
        by_source: Counter = Counter()
        scanned, offset, truncated = 0, None, False
        while True:
            records, offset = vector_store.scroll(ws, limit=PAGE, offset=offset)
            by_source.update(point.payload["source"] for point in records)
            scanned += len(records)
            if offset is None:
                break
            if scanned >= settings.SWEEP_SCAN_CAP:
                truncated = True
                break
        return Stats(
            total=total,
            active=total - superseded,
            superseded=superseded,
            pinned=vector_store.count(ws, must=[vector_store._field("pinned", True)]),
            never_recalled=vector_store.count(
                ws, must=[vector_store._field("recall_count", 0)]
            ),
            by_source=dict(by_source),
            by_source_truncated=truncated,
        )

    # -- delete --------------------------------------------------------------

    def forget(self, point_id: str) -> None:
        vector_store.delete([point_id], self.workspace_id)

    def purge(self, confirm: str) -> int:
        """Delete every memory in the workspace. `confirm` must equal the workspace id."""
        if confirm != self.workspace_id:
            raise ConfirmationRequired("confirm must equal the workspace id")
        deleted = vector_store.delete_workspace(self.workspace_id)
        logger.warning("purge: workspace=%s deleted=%d", self.workspace_id, deleted)
        return deleted

    # -- maintenance ---------------------------------------------------------

    def _exclusive(self):
        lock = _maintenance_locks[self.workspace_id]
        if not lock.acquire(blocking=False):
            raise MaintenanceBusy(self.workspace_id)
        return lock

    def run_cleanup(self, retention_days: int = 30) -> int:
        return self.sweep(retention_days).deleted

    def sweep(self, retention_days: int = 30, dry_run: bool = False) -> SweepResult:
        """Drop old, never-recalled, unpinned memories the LLM judges irrelevant, and
        purge superseded memories past retention. Unsure or malformed answers keep."""
        lock = self._exclusive()
        try:
            cutoff = time.time() - retention_days * DAY
            candidates, offset = [], None
            while len(candidates) < settings.SWEEP_SCAN_CAP:
                records, offset = vector_store.find_cleanup_candidates(
                    self.workspace_id, cutoff, limit=PAGE, offset=offset
                )
                candidates.extend(records)
                if offset is None:
                    break
            to_delete: list[str] = []
            judged = [
                point
                for point in candidates
                if point.payload.get("importance", 3) < IMPORTANT
            ]
            for start in range(0, len(judged), settings.SWEEP_BATCH_SIZE):
                batch = judged[start : start + settings.SWEEP_BATCH_SIZE]
                refs = {f"m{index}": point for index, point in enumerate(batch)}
                try:
                    reply = inference.chat(
                        "memory_steward.cleanup_relevance",
                        prompts.sweep_prompt(
                            [(ref, p.payload["text"]) for ref, p in refs.items()]
                        ),
                    )
                except Exception as exc:
                    logger.warning("sweep: batch skipped, keeping memories: %s", exc)
                    continue
                decisions = prompts.parse_sweep(reply, list(refs))
                to_delete.extend(
                    str(point.id) for ref, point in refs.items() if not decisions[ref]
                )
            stale = vector_store.find_superseded_before(self.workspace_id, cutoff)
            stale_ids = [str(point.id) for point in stale]
            if not dry_run:
                for ids in (to_delete, stale_ids):
                    if ids:
                        vector_store.delete(ids, self.workspace_id)
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
        finally:
            lock.release()

    def on_sprint_completed(self, retention_days: int = 30) -> int:
        return self.run_cleanup(retention_days=retention_days)

    def organize(self, dry_run: bool = False, max_clusters: int = 50) -> OrganizeResult:
        """Find near-duplicate or contradicting memories and soft-supersede the losers.
        Nothing is hard-deleted here; sweep purges superseded memories after retention."""
        lock = self._exclusive()
        try:
            seen: set[str] = set()
            clusters = superseded = merged = scanned = 0
            offset = None
            while clusters < max_clusters and scanned < settings.ORGANIZE_SCAN_CAP:
                records, offset = vector_store.scroll(
                    self.workspace_id, limit=PAGE, offset=offset, with_vectors=True
                )
                scanned += len(records)
                for record in records:
                    if clusters >= max_clusters:
                        break
                    if str(record.id) in seen:
                        continue
                    group = [
                        hit
                        for hit in vector_store.search(
                            record.vector, self.workspace_id, limit=6
                        )
                        if hit.score >= settings.MEMORY_ORGANIZE_THRESHOLD
                        and str(hit.id) not in seen
                    ]
                    seen.update(str(hit.id) for hit in group)
                    seen.add(str(record.id))
                    if len(group) < 2:
                        continue
                    outcome = self._organize_group(group, dry_run)
                    if outcome is not None:
                        clusters += 1
                        superseded += outcome[0]
                        merged += outcome[1]
                if offset is None:
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
        finally:
            lock.release()

    def _organize_group(self, group, dry_run: bool) -> tuple[int, int] | None:
        ordered = sorted(group, key=lambda hit: hit.payload["timestamp"])
        refs = {f"m{index}": hit for index, hit in enumerate(ordered)}
        try:
            reply = inference.chat(
                "memory_steward.organize",
                prompts.organize_prompt(
                    [(ref, hit.payload["text"]) for ref, hit in refs.items()]
                ),
            )
        except Exception as exc:
            logger.warning("organize: group skipped: %s", exc)
            return None
        verdict = prompts.parse_organize(reply, list(refs))
        if verdict["action"] == "distinct":
            return None
        now = time.time()
        if verdict["action"] == "merge":
            losers = [hit for hit in ordered if not hit.payload.get("pinned")]
            if not losers:
                return None
            if not dry_run:
                fact = verdict["text"]
                top = max(hit.payload.get("importance", 3) for hit in ordered)
                new_id = vector_store.upsert(
                    embedding=vectorizer.embed_one(fact),
                    workspace_id=self.workspace_id,
                    text=fact,
                    source="organize",
                    agent="memory_steward",
                    timestamp=now,
                    importance=top,
                )
                self._supersede(losers, new_id, now)
            return len(losers), 1
        keeper = refs[verdict["keep"]]
        losers = [
            hit
            for hit in ordered
            if hit.id != keeper.id and not hit.payload.get("pinned")
        ]
        if not losers:
            return None
        if not dry_run:
            self._supersede(losers, str(keeper.id), now)
        return len(losers), 0

    def _supersede(self, losers, keeper_id: str, now: float) -> None:
        vector_store.set_payload(
            [str(hit.id) for hit in losers],
            self.workspace_id,
            {"status": "superseded", "superseded_by": keeper_id, "superseded_at": now},
        )
