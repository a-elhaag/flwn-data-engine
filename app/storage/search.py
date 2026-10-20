"""Search inside uploaded files: by meaning and by exact words, reranked, with citations.

Same recipe as memory recall (vector + keyword search merged by rank fusion, then a reranking
model), over file chunks. Only chunks of live, verified, fully indexed files are searched, so a
deleted or quarantined file never appears.
"""

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import and_, func, select

from app.clients import inference
from app.config import settings
from app.db.models.files import File
from app.db.models.memory import Chunk
from app.db.session import session_for, tune_vector_search
from app.memory.recall import fuse, relevance

logger = logging.getLogger(__name__)


@dataclass
class FileHit:
    file_id: str
    file_name: str
    chunk_index: int
    page: int | None  # PDF page, when known
    heading_path: str | None  # e.g. "Design > Auth", for Markdown
    text: str
    score: float  # relevance to the query


def search_files(
    workspace_id: str,
    query: str,
    limit: int = 5,
    kind: str | None = None,
    project_id: uuid.UUID | None = None,
) -> list[FileHit]:
    vector = inference.embed(query)
    breadth = min(max(limit * 3, 10), settings.RECALL_CANDIDATES)
    workspace = uuid.UUID(workspace_id)

    def searchable(statement):
        statement = statement.join(
            File, and_(File.workspace_id == Chunk.workspace_id, File.id == Chunk.source_id)
        ).where(
            Chunk.workspace_id == workspace,
            Chunk.source_type == "file",
            File.deleted_at.is_(None),
            File.status == "ready",
            File.index_status == "done",
        )
        if kind:
            statement = statement.where(File.kind == kind)
        if project_id:
            statement = statement.where(File.project_id == project_id)
        return statement

    with session_for(workspace_id) as session:
        distance = Chunk.embedding.cosine_distance(vector)
        tune_vector_search(session, breadth)
        by_meaning = session.execute(
            searchable(select(Chunk, File.name, (1 - distance).label("score")))
            .where(Chunk.embedding.is_not(None))
            .order_by(distance)
            .limit(breadth)
        ).all()
        tsquery = func.websearch_to_tsquery("simple", query)
        by_words = session.execute(
            searchable(select(Chunk, File.name))
            .where(Chunk.tsv.op("@@")(tsquery))
            .order_by(func.ts_rank_cd(Chunk.tsv, tsquery).desc(), Chunk.id)
            .limit(breadth)
        ).all()

        names = {chunk.id: name for chunk, name, _ in by_meaning}
        names.update({chunk.id: name for chunk, name in by_words})
        similarity = {chunk.id: float(score) for chunk, _, score in by_meaning}
        candidates = fuse([c for c, _, _ in by_meaning], [c for c, _ in by_words])
        candidates = candidates[: settings.RECALL_CANDIDATES]
        missing = [chunk.id for chunk in candidates if chunk.id not in similarity]
        if missing:
            rows = session.execute(
                select(Chunk.id, (1 - distance).label("score")).where(Chunk.id.in_(missing))
            )
            similarity.update({chunk_id: float(score) for chunk_id, score in rows})
        scores = relevance(query, candidates, similarity)
        ranked = sorted(candidates, key=lambda chunk: scores.get(chunk.id, 0.0), reverse=True)[
            :limit
        ]
        return [
            FileHit(
                file_id=str(chunk.source_id),
                file_name=names[chunk.id],
                chunk_index=chunk.chunk_index,
                page=chunk.page,
                heading_path=chunk.heading_path,
                text=chunk.text,
                score=scores.get(chunk.id, 0.0),
            )
            for chunk in ranked
        ]
