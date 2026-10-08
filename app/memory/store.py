"""Memory persistence: every query against the memories table, always scoped to one workspace.

Two layers keep workspaces apart. Each query filters on workspace_id, and the session sets
app.workspace_id so row-level security rejects anything the filter missed.
"""

import uuid
from datetime import UTC, datetime

import psycopg
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, undefer

from app.db.models.memory import Memory
from app.db.session import tune_vector_search
from app.memory.errors import WorkspaceNotFound


def to_datetime(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=UTC)


def stored_at(row: Memory) -> float:
    """When the memory was last stored: its creation, or a later duplicate write that refreshed it."""
    return max(row.created_at, row.refreshed_at or row.created_at).timestamp()


def _as_uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(value)


class MemoryStore:
    def __init__(self, session: Session, workspace_id: str | uuid.UUID):
        self.session = session
        self.workspace_id = _as_uuid(workspace_id)

    def _visible(self, include_superseded: bool = False):
        query = select(Memory).where(Memory.workspace_id == self.workspace_id)
        return query if include_superseded else query.where(Memory.status != "superseded")

    # -- write ---------------------------------------------------------------

    def insert(
        self,
        *,
        text: str,
        raw_text: str | None,
        source: str,
        agent: str,
        importance: int,
        embedding: list[float],
        embedding_model: str,
        now: float,
    ) -> Memory:
        row = Memory(
            workspace_id=self.workspace_id,
            kind="fact",
            text=text,
            raw_text=raw_text,
            source_type=source,
            agent_name=agent,
            importance=importance,
            embedding=embedding,
            embedding_model=embedding_model,
            created_at=to_datetime(now),
        )
        self.session.add(row)
        try:
            self.session.flush()
        except IntegrityError as exc:
            if isinstance(exc.orig, psycopg.errors.ForeignKeyViolation):
                raise WorkspaceNotFound(str(self.workspace_id)) from exc
            raise
        return row

    def refresh(self, row: Memory, now: float, importance: int) -> None:
        """A duplicate write re-confirms an existing memory: it counts as stored again."""
        row.refreshed_at = to_datetime(now)
        row.importance = max(row.importance, importance)

    def replace(self, row: Memory, text: str, embedding: list[float], embedding_model: str) -> None:
        row.text = text
        row.embedding = embedding
        row.embedding_model = embedding_model

    def touch(self, ids: list[uuid.UUID], now: float) -> None:
        """Count a recall. One atomic UPDATE, so concurrent recalls and replicas never lose counts."""
        if ids:
            self.session.execute(
                update(Memory)
                .where(Memory.workspace_id == self.workspace_id, Memory.id.in_(ids))
                .values(recall_count=Memory.recall_count + 1, last_recalled_at=to_datetime(now)),
                execution_options={"synchronize_session": False},
            )

    def supersede(self, ids: list[uuid.UUID], keeper_id: uuid.UUID, now: float) -> None:
        self.session.execute(
            update(Memory)
            .where(Memory.workspace_id == self.workspace_id, Memory.id.in_(ids))
            .values(status="superseded", superseded_by=keeper_id, superseded_at=to_datetime(now)),
            execution_options={"synchronize_session": False},
        )

    def delete(self, ids: list[uuid.UUID], *, only_unused: bool = False) -> int:
        """Delete by id. `only_unused` skips memories recalled or pinned since they were chosen."""
        if not ids:
            return 0
        query = delete(Memory).where(Memory.workspace_id == self.workspace_id, Memory.id.in_(ids))
        if only_unused:
            query = query.where(Memory.recall_count == 0, Memory.pinned.is_(False))
        return self.session.execute(query).rowcount

    def delete_all(self) -> int:
        return self.session.execute(
            delete(Memory).where(Memory.workspace_id == self.workspace_id)
        ).rowcount

    # -- read ----------------------------------------------------------------

    def get(self, memory_id: str | uuid.UUID, *, with_raw: bool = False) -> Memory | None:
        query = select(Memory).where(
            Memory.workspace_id == self.workspace_id, Memory.id == _as_uuid(memory_id)
        )
        if with_raw:
            query = query.options(undefer(Memory.raw_text))
        return self.session.scalars(query).one_or_none()

    def search(
        self, embedding: list[float], limit: int, sources: list[str] | None = None
    ) -> list[tuple[Memory, float]]:
        """Nearest memories by cosine similarity (1.0 = identical), superseded ones hidden."""
        distance = Memory.embedding.cosine_distance(embedding)
        query = (
            select(Memory, (1 - distance).label("score"))
            .where(
                Memory.workspace_id == self.workspace_id,
                Memory.status != "superseded",
                Memory.embedding.is_not(None),
            )
            .order_by(distance)
            .limit(limit)
        )
        if sources:
            query = query.where(Memory.source_type.in_(sources))
        tune_vector_search(self.session, limit)
        return [(row, float(score)) for row, score in self.session.execute(query)]

    def keyword_search(
        self, query: str, limit: int, sources: list[str] | None = None
    ) -> list[Memory]:
        """Memories whose text matches the query's words, best match first.

        Finds what embeddings miss: ticket ids, names, exact phrases. The 'simple' text-search
        configuration does no stemming, so it works for mixed languages.
        """
        tsquery = func.websearch_to_tsquery("simple", query)
        statement = (
            select(Memory)
            .where(
                Memory.workspace_id == self.workspace_id,
                Memory.status != "superseded",
                Memory.tsv.op("@@")(tsquery),
            )
            .order_by(func.ts_rank_cd(Memory.tsv, tsquery).desc(), Memory.id)
            .limit(limit)
        )
        if sources:
            statement = statement.where(Memory.source_type.in_(sources))
        return list(self.session.scalars(statement))

    def similarities(self, embedding: list[float], ids: list[uuid.UUID]) -> dict[uuid.UUID, float]:
        """Cosine similarity of the given memories to a vector (for hits the vector search missed)."""
        if not ids:
            return {}
        distance = Memory.embedding.cosine_distance(embedding)
        rows = self.session.execute(
            select(Memory.id, (1 - distance).label("score")).where(
                Memory.workspace_id == self.workspace_id,
                Memory.id.in_(ids),
                Memory.embedding.is_not(None),
            )
        )
        return {memory_id: float(score) for memory_id, score in rows}

    def page(
        self,
        limit: int,
        cursor: str | None = None,
        *,
        source: str | None = None,
        agent: str | None = None,
        include_superseded: bool = False,
        with_vectors: bool = False,
    ) -> tuple[list[Memory], str | None]:
        """One page in id order. The cursor is the last id of the previous page."""
        query = self._visible(include_superseded).order_by(Memory.id).limit(limit + 1)
        if cursor:
            query = query.where(Memory.id > _as_uuid(cursor))
        if source:
            query = query.where(Memory.source_type == source)
        if agent:
            query = query.where(Memory.agent_name == agent)
        if with_vectors:
            query = query.options(undefer(Memory.embedding))
        rows = list(self.session.scalars(query))
        if len(rows) > limit:
            rows = rows[:limit]
            return rows, str(rows[-1].id)
        return rows, None

    def stats(self) -> dict:
        in_workspace = Memory.workspace_id == self.workspace_id
        live = Memory.status != "superseded"
        count = lambda *conditions: self.session.scalar(  # noqa: E731
            select(func.count()).select_from(Memory).where(in_workspace, *conditions)
        )
        by_source = self.session.execute(
            select(Memory.source_type, func.count())
            .where(in_workspace, live)
            .group_by(Memory.source_type)
        )
        return {
            "total": count(),
            "superseded": count(Memory.status == "superseded"),
            "pinned": count(live, Memory.pinned.is_(True)),
            "never_recalled": count(live, Memory.recall_count == 0),
            "by_source": {(source or "unknown"): n for source, n in by_source},
        }

    def cleanup_candidates(self, cutoff: float, limit: int):
        """Old, never-recalled, unpinned, still-live memories (id, text, importance)."""
        last_stored = func.greatest(
            Memory.created_at, func.coalesce(Memory.refreshed_at, Memory.created_at)
        )
        return self.session.execute(
            select(Memory.id, Memory.text, Memory.importance)
            .where(
                Memory.workspace_id == self.workspace_id,
                Memory.status != "superseded",
                Memory.pinned.is_(False),
                Memory.recall_count == 0,
                last_stored < to_datetime(cutoff),
            )
            .order_by(Memory.created_at)
            .limit(limit)
        ).all()

    def superseded_before(self, cutoff: float, limit: int = 1000) -> list[uuid.UUID]:
        return list(
            self.session.scalars(
                select(Memory.id)
                .where(
                    Memory.workspace_id == self.workspace_id,
                    Memory.status == "superseded",
                    Memory.pinned.is_(False),
                    Memory.superseded_at < to_datetime(cutoff),
                )
                .limit(limit)
            )
        )
