"""Qdrant memory storage. Every operation requires workspace isolation."""

import threading
import uuid

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from config import settings

COLLECTION = "memory"
EMBEDDING_SIZE = 1536
_touch_lock = threading.Lock()


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
    client.create_payload_index(
        COLLECTION, field_name="workspace_id", field_schema="keyword"
    )


def _workspace_filter(workspace_id: str) -> qmodels.Filter:
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        raise TypeError("workspace_id is required")
    return qmodels.Filter(
        must=[
            qmodels.FieldCondition(
                key="workspace_id", match=qmodels.MatchValue(value=workspace_id)
            )
        ]
    )


def upsert(
    embedding: list[float],
    workspace_id: str,
    text: str,
    source: str,
    agent: str,
    timestamp: float,
) -> str:
    _workspace_filter(workspace_id)
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
                    "source": source,
                    "agent": agent,
                    "timestamp": timestamp,
                    "last_recalled_at": None,
                    "recall_count": 0,
                },
            )
        ],
    )
    return point_id


def search(
    embedding: list[float], workspace_id: str, limit: int
) -> list[qmodels.ScoredPoint]:
    return (
        _client()
        .query_points(
            COLLECTION,
            query=embedding,
            limit=limit,
            query_filter=_workspace_filter(workspace_id),
        )
        .points
    )


def touch(point_id: str, workspace_id: str, recalled_at: float) -> None:
    _workspace_filter(workspace_id)
    with _touch_lock:
        client = _client()
        points = client.retrieve(COLLECTION, ids=[point_id], with_payload=True)
        if not points or points[0].payload.get("workspace_id") != workspace_id:
            return
        client.set_payload(
            COLLECTION,
            payload={
                "last_recalled_at": recalled_at,
                "recall_count": points[0].payload.get("recall_count", 0) + 1,
            },
            points=[point_id],
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


def find_cleanup_candidates(
    workspace_id: str, cutoff_timestamp: float
) -> list[qmodels.Record]:
    workspace_filter = _workspace_filter(workspace_id)
    records, _ = _client().scroll(
        COLLECTION,
        scroll_filter=qmodels.Filter(
            must=[
                *workspace_filter.must,
                qmodels.FieldCondition(
                    key="timestamp", range=qmodels.Range(lt=cutoff_timestamp)
                ),
                qmodels.FieldCondition(
                    key="recall_count", match=qmodels.MatchValue(value=0)
                ),
            ]
        ),
        with_payload=True,
        limit=1000,
    )
    return records
