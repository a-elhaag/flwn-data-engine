"""Qdrant memory storage. Every operation requires workspace isolation."""

import threading
import uuid
from functools import lru_cache

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from config import settings

COLLECTION = "memory"
EMBEDDING_SIZE = 1536
_touch_lock = threading.Lock()
_INDEXES = {
    "workspace_id": "keyword",
    "source": "keyword",
    "agent": "keyword",
    "status": "keyword",
    "pinned": "bool",
    "timestamp": "float",
    "recall_count": "integer",
    "superseded_at": "float",
}


@lru_cache
def _client() -> QdrantClient:
    return QdrantClient(
        url=settings.QDRANT_URL,
        api_key=settings.QDRANT_API_KEY or None,
        port=None,
        timeout=30,
    )


def ensure_collection() -> None:
    client = _client()
    if COLLECTION not in {
        collection.name for collection in client.get_collections().collections
    }:
        client.create_collection(
            COLLECTION,
            vectors_config=qmodels.VectorParams(
                size=EMBEDDING_SIZE, distance=qmodels.Distance.COSINE
            ),
        )
    for field, schema in _INDEXES.items():
        client.create_payload_index(COLLECTION, field_name=field, field_schema=schema)


def _require_workspace(workspace_id: str) -> None:
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        raise TypeError("workspace_id is required")


def _workspace_condition(workspace_id: str) -> qmodels.FieldCondition:
    _require_workspace(workspace_id)
    return qmodels.FieldCondition(
        key="workspace_id", match=qmodels.MatchValue(value=workspace_id)
    )


def _workspace_filter(workspace_id: str) -> qmodels.Filter:
    return qmodels.Filter(must=[_workspace_condition(workspace_id)])


def _field(key: str, value) -> qmodels.FieldCondition:
    return qmodels.FieldCondition(key=key, match=qmodels.MatchValue(value=value))


def _build_filter(
    workspace_id: str,
    must: list | None = None,
    must_not: list | None = None,
    include_superseded: bool = False,
) -> qmodels.Filter:
    """Always includes the workspace condition. Superseded memories are hidden by
    default; a missing `status` (legacy points) counts as active."""
    excluded = list(must_not or [])
    if not include_superseded:
        excluded.append(_field("status", "superseded"))
    return qmodels.Filter(
        must=[_workspace_condition(workspace_id), *(must or [])],
        must_not=excluded or None,
    )


def upsert(
    embedding: list[float],
    workspace_id: str,
    text: str,
    source: str,
    agent: str,
    timestamp: float,
    *,
    raw_text: str | None = None,
    importance: int = 3,
) -> str:
    _require_workspace(workspace_id)
    point_id = str(uuid.uuid4())
    _client().upsert(
        COLLECTION,
        points=[
            qmodels.PointStruct(
                id=point_id,
                vector=embedding,
                payload={
                    "workspace_id": workspace_id,
                    "text": text,
                    "raw_text": raw_text,
                    "source": source,
                    "agent": agent,
                    "timestamp": timestamp,
                    "last_recalled_at": None,
                    "recall_count": 0,
                    "importance": importance,
                    "pinned": False,
                    "status": "active",
                },
            )
        ],
    )
    return point_id


def search(
    embedding: list[float],
    workspace_id: str,
    limit: int,
    sources: list[str] | None = None,
) -> list[qmodels.ScoredPoint]:
    must = [qmodels.FieldCondition(key="source", match=qmodels.MatchAny(any=sources))] if sources else None
    return (
        _client()
        .query_points(
            COLLECTION,
            query=embedding,
            limit=limit,
            query_filter=_build_filter(workspace_id, must=must),
            with_payload=True,
        )
        .points
    )


def get(
    point_id: str, workspace_id: str, with_vector: bool = False
) -> qmodels.Record | None:
    _require_workspace(workspace_id)
    points = _client().retrieve(
        COLLECTION, ids=[point_id], with_payload=True, with_vectors=with_vector
    )
    if not points or points[0].payload.get("workspace_id") != workspace_id:
        return None
    return points[0]


def replace(
    point_id: str, workspace_id: str, embedding: list[float], payload: dict
) -> None:
    """Overwrite one existing point. Payload must already belong to the workspace."""
    _require_workspace(workspace_id)
    if payload.get("workspace_id") != workspace_id:
        raise ValueError("payload workspace mismatch")
    _client().upsert(
        COLLECTION,
        points=[qmodels.PointStruct(id=point_id, vector=embedding, payload=payload)],
    )


def set_payload(ids: list[str], workspace_id: str, payload: dict) -> None:
    if "workspace_id" in payload:
        raise ValueError("workspace_id cannot be changed")
    _client().set_payload(
        COLLECTION,
        payload=payload,
        points=qmodels.Filter(
            must=[
                qmodels.HasIdCondition(has_id=ids),
                _workspace_condition(workspace_id),
            ]
        ),
    )


def touch(point_id: str, workspace_id: str, recalled_at: float) -> None:
    touch_many([point_id], workspace_id, recalled_at)


def touch_many(ids: list[str], workspace_id: str, recalled_at: float) -> None:
    """Bump recall counters. Read-modify-write under a process-local lock, so counts
    can under-count when several replicas touch the same point at once."""
    _require_workspace(workspace_id)
    if not ids:
        return
    with _touch_lock:
        client = _client()
        for point in client.retrieve(COLLECTION, ids=ids, with_payload=True):
            if point.payload.get("workspace_id") != workspace_id:
                continue
            client.set_payload(
                COLLECTION,
                payload={
                    "last_recalled_at": recalled_at,
                    "recall_count": point.payload.get("recall_count", 0) + 1,
                },
                points=[point.id],
            )


def delete(ids: list[str], workspace_id: str) -> None:
    workspace_filter = _workspace_filter(workspace_id)
    _client().delete(
        COLLECTION,
        points_selector=qmodels.FilterSelector(
            filter=qmodels.Filter(
                must=[qmodels.HasIdCondition(has_id=ids), *workspace_filter.must],
            )
        ),
    )


def scroll(
    workspace_id: str,
    *,
    must: list | None = None,
    must_not: list | None = None,
    include_superseded: bool = False,
    limit: int = 100,
    offset=None,
    with_vectors: bool = False,
) -> tuple[list[qmodels.Record], object]:
    return _client().scroll(
        COLLECTION,
        scroll_filter=_build_filter(workspace_id, must, must_not, include_superseded),
        with_payload=True,
        with_vectors=with_vectors,
        limit=limit,
        offset=offset,
    )


def count(
    workspace_id: str,
    *,
    must: list | None = None,
    must_not: list | None = None,
    include_superseded: bool = False,
) -> int:
    return (
        _client()
        .count(
            COLLECTION,
            count_filter=_build_filter(workspace_id, must, must_not, include_superseded),
            exact=True,
        )
        .count
    )


def delete_workspace(workspace_id: str) -> int:
    """Remove every memory of one workspace. Returns how many existed."""
    existing = count(workspace_id, include_superseded=True)
    _client().delete(
        COLLECTION,
        points_selector=qmodels.FilterSelector(
            filter=_build_filter(workspace_id, include_superseded=True)
        ),
    )
    return existing


def find_cleanup_candidates(
    workspace_id: str, cutoff_timestamp: float, limit: int = 1000, offset=None
) -> tuple[list[qmodels.Record], object]:
    """Old, never-recalled, unpinned, still-active memories."""
    return scroll(
        workspace_id,
        must=[
            qmodels.FieldCondition(
                key="timestamp", range=qmodels.Range(lt=cutoff_timestamp)
            ),
            _field("recall_count", 0),
        ],
        must_not=[_field("pinned", True)],
        limit=limit,
        offset=offset,
    )


def find_superseded_before(
    workspace_id: str, cutoff_timestamp: float, limit: int = 1000
) -> list[qmodels.Record]:
    records, _ = scroll(
        workspace_id,
        must=[
            _field("status", "superseded"),
            qmodels.FieldCondition(
                key="superseded_at", range=qmodels.Range(lt=cutoff_timestamp)
            ),
        ],
        must_not=[_field("pinned", True)],
        include_superseded=True,
        limit=limit,
    )
    return records
