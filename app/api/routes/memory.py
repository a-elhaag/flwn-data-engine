"""Workspace memory: write, recall, browse, revise, pin, forget, and maintenance."""

from dataclasses import asdict
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.api.deps import ADMIN, DELETE, READ, WRITE, Memory
from app.api.schemas import (
    AnchorRequest,
    CleanupRequest,
    CleanupResponse,
    IngestRequest,
    IngestResponse,
    OrganizeRequest,
    RecallRequest,
    RememberRequest,
    RememberResponse,
    ReviseRequest,
)
from app.memory.recall import RankedResult
from app.memory.steward import BrowsePage, MemoryRecord, OrganizeResult, Stats

router = APIRouter(prefix="/workspaces/{workspace_id}")


def _stored(result) -> RememberResponse:
    return RememberResponse(point_id=result.point_id, deduplicated=result.deduplicated)


@router.post("/memories", dependencies=[WRITE])
def remember(request: RememberRequest, memory: Memory) -> RememberResponse:
    return _stored(memory.remember_detailed(**request.model_dump()))


@router.post("/memories/batch", dependencies=[WRITE])
def ingest(request: IngestRequest, memory: Memory) -> IngestResponse:
    results = memory.ingest([item.model_dump() for item in request.items])
    return IngestResponse(results=[_stored(result) for result in results])


@router.post("/memories/recall", dependencies=[READ])
def recall(request: RecallRequest, memory: Memory) -> list[RankedResult]:
    return memory.recall(**request.model_dump())


@router.get("/memories", dependencies=[READ])
def browse(
    memory: Memory,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: UUID | None = None,
    source: str | None = None,
    agent: str | None = None,
    include_superseded: bool = False,
) -> BrowsePage:
    return memory.browse(limit, str(cursor) if cursor else None, source, agent, include_superseded)


@router.get("/memories/stats", dependencies=[READ])
def stats(memory: Memory) -> Stats:
    return memory.stats()


@router.post("/memories/cleanup", dependencies=[ADMIN])
def cleanup(request: CleanupRequest, memory: Memory) -> CleanupResponse:
    return CleanupResponse(**asdict(memory.sweep(**request.model_dump())))


@router.post("/memories/organize", dependencies=[ADMIN])
def organize(request: OrganizeRequest, memory: Memory) -> OrganizeResult:
    return memory.organize(**request.model_dump())


@router.post("/sprint-completed", dependencies=[ADMIN])
def sprint_completed(request: CleanupRequest, memory: Memory) -> CleanupResponse:
    return cleanup(request, memory)


@router.get("/memories/{point_id}", dependencies=[READ])
def open_memory(point_id: UUID, memory: Memory) -> MemoryRecord:
    return memory.open(str(point_id))


@router.patch("/memories/{point_id}", dependencies=[WRITE])
def revise(point_id: UUID, request: ReviseRequest, memory: Memory) -> MemoryRecord:
    return memory.revise(str(point_id), request.text)


@router.put("/memories/{point_id}/pin", dependencies=[WRITE])
def anchor(point_id: UUID, request: AnchorRequest, memory: Memory) -> MemoryRecord:
    return memory.anchor(str(point_id), request.pinned)


@router.delete("/memories/{point_id}", status_code=204, dependencies=[DELETE])
def forget(point_id: UUID, memory: Memory) -> Response:
    memory.forget(str(point_id))
    return Response(status_code=204)


@router.delete("/memories", dependencies=[ADMIN])
def purge(memory: Memory, confirm: str = "") -> dict[str, int]:
    return {"deleted": memory.purge(confirm)}
