"""Internal FastAPI data API. Only this service accesses memory storage."""

from dataclasses import asdict
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from api import tokens
from api.auth import WorkspaceId, allow, service_only
from clients.health import health_check
from memory.errors import ConfirmationRequired, MaintenanceBusy, MemoryNotFound
from memory.steward import (
    BrowsePage,
    MemoryRecord,
    MemorySteward,
    OrganizeResult,
    Stats,
    SweepResult,
)
from retrieval.search import RankedResult

router = APIRouter()


def steward(workspace_id: WorkspaceId) -> MemorySteward:
    return MemorySteward(workspace_id)


Memory = Annotated[MemorySteward, Depends(steward)]
NonBlank = Annotated[str, Field(min_length=1, max_length=100000, pattern=r"\S")]
READ, WRITE, DELETE, ADMIN = (
    allow(tokens.SCOPE_READ),
    allow(tokens.SCOPE_WRITE),
    allow(tokens.SCOPE_DELETE),
    allow(None),
)


class RememberRequest(BaseModel):
    text: NonBlank
    source: NonBlank
    agent: NonBlank


class RememberResponse(BaseModel):
    point_id: str
    deduplicated: bool = False


class IngestRequest(BaseModel):
    items: list[RememberRequest] = Field(min_length=1, max_length=50)


class IngestResponse(BaseModel):
    results: list[RememberResponse]


class RecallRequest(BaseModel):
    query: NonBlank
    agent: NonBlank
    limit: int = Field(default=5, ge=1, le=100)
    sources: list[NonBlank] | None = Field(default=None, max_length=20)


class ReviseRequest(BaseModel):
    text: NonBlank


class AnchorRequest(BaseModel):
    pinned: bool = True


class CleanupRequest(BaseModel):
    retention_days: int = Field(default=30, ge=1, le=36500)
    dry_run: bool = False


class CleanupResponse(BaseModel):
    deleted: int
    scanned: int = 0
    kept: int = 0
    superseded_purged: int = 0
    dry_run: bool = False


class OrganizeRequest(BaseModel):
    dry_run: bool = False
    max_clusters: int = Field(default=50, ge=1, le=500)


class TokenRequest(BaseModel):
    workspace_id: Annotated[str, Field(min_length=1, max_length=200, pattern=r"^\S+$")]
    subject: NonBlank = "agent"
    scopes: list[str] = sorted(tokens.AGENT_SCOPES)
    ttl_seconds: int = Field(default=3600, ge=1)


class TokenResponse(BaseModel):
    token: str
    expires_at: int


def _not_found(exc: MemoryNotFound) -> HTTPException:
    return HTTPException(status_code=404, detail="Memory not found")


def _busy(exc: MaintenanceBusy) -> HTTPException:
    return HTTPException(status_code=409, detail="Maintenance already running")


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", dependencies=[Depends(service_only)])
def readyz(response: Response) -> dict[str, bool]:
    status = health_check()
    if not all(status.values()):
        response.status_code = 503
    return status


@router.post("/auth/tokens", dependencies=[Depends(service_only)])
def mint_token(request: TokenRequest) -> TokenResponse:
    try:
        token, expires_at = tokens.mint(
            request.workspace_id,
            set(request.scopes),
            request.subject,
            request.ttl_seconds,
        )
    except tokens.TokenError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return TokenResponse(token=token, expires_at=expires_at)


@router.post("/workspaces/{workspace_id}/memories", dependencies=[WRITE])
def remember(request: RememberRequest, memory: Memory) -> RememberResponse:
    result = memory.remember_detailed(**request.model_dump())
    return RememberResponse(
        point_id=result.point_id, deduplicated=result.deduplicated
    )


@router.post("/workspaces/{workspace_id}/memories/batch", dependencies=[WRITE])
def ingest(request: IngestRequest, memory: Memory) -> IngestResponse:
    results = memory.ingest([item.model_dump() for item in request.items])
    return IngestResponse(
        results=[
            RememberResponse(point_id=r.point_id, deduplicated=r.deduplicated)
            for r in results
        ]
    )


@router.post("/workspaces/{workspace_id}/memories/recall", dependencies=[READ])
def recall(request: RecallRequest, memory: Memory) -> list[RankedResult]:
    return memory.recall(**request.model_dump())


@router.get("/workspaces/{workspace_id}/memories", dependencies=[READ])
def browse(
    memory: Memory,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: UUID | None = None,
    source: str | None = None,
    agent: str | None = None,
    include_superseded: bool = False,
) -> BrowsePage:
    return memory.browse(
        limit, str(cursor) if cursor else None, source, agent, include_superseded
    )


@router.get("/workspaces/{workspace_id}/memories/stats", dependencies=[READ])
def stats(memory: Memory) -> Stats:
    return memory.stats()


@router.post("/workspaces/{workspace_id}/memories/cleanup", dependencies=[ADMIN])
def cleanup(request: CleanupRequest, memory: Memory) -> CleanupResponse:
    try:
        return CleanupResponse(**asdict(memory.sweep(**request.model_dump())))
    except MaintenanceBusy as exc:
        raise _busy(exc)


@router.post("/workspaces/{workspace_id}/memories/organize", dependencies=[ADMIN])
def organize(request: OrganizeRequest, memory: Memory) -> OrganizeResult:
    try:
        return memory.organize(**request.model_dump())
    except MaintenanceBusy as exc:
        raise _busy(exc)


@router.post("/workspaces/{workspace_id}/sprint-completed", dependencies=[ADMIN])
def sprint_completed(request: CleanupRequest, memory: Memory) -> CleanupResponse:
    return cleanup(request, memory)


@router.get("/workspaces/{workspace_id}/memories/{point_id}", dependencies=[READ])
def open_memory(point_id: UUID, memory: Memory) -> MemoryRecord:
    try:
        return memory.open(str(point_id))
    except MemoryNotFound as exc:
        raise _not_found(exc)


@router.patch("/workspaces/{workspace_id}/memories/{point_id}", dependencies=[WRITE])
def revise(point_id: UUID, request: ReviseRequest, memory: Memory) -> MemoryRecord:
    try:
        return memory.revise(str(point_id), request.text)
    except MemoryNotFound as exc:
        raise _not_found(exc)


@router.put("/workspaces/{workspace_id}/memories/{point_id}/pin", dependencies=[WRITE])
def anchor(point_id: UUID, request: AnchorRequest, memory: Memory) -> MemoryRecord:
    try:
        return memory.anchor(str(point_id), request.pinned)
    except MemoryNotFound as exc:
        raise _not_found(exc)


@router.delete(
    "/workspaces/{workspace_id}/memories/{point_id}",
    status_code=204,
    dependencies=[DELETE],
)
def forget(point_id: UUID, memory: Memory) -> Response:
    memory.forget(str(point_id))
    return Response(status_code=204)


@router.delete("/workspaces/{workspace_id}/memories", dependencies=[ADMIN])
def purge(memory: Memory, confirm: str = "") -> dict[str, int]:
    try:
        return {"deleted": memory.purge(confirm)}
    except ConfirmationRequired:
        raise HTTPException(
            status_code=400, detail="confirm query parameter must equal the workspace id"
        )
