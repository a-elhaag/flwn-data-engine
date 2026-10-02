"""Internal FastAPI data API. Only this service accesses memory storage."""

import secrets
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Response
from pydantic import BaseModel, Field

from flwn_data import vector_store
from flwn_data.config import settings
from flwn_data.memory_steward import MemorySteward, RankedResult, health_check


@asynccontextmanager
async def lifespan(app: FastAPI):
    vector_store.ensure_collection()
    yield


def authenticate(x_data_api_key: Annotated[str | None, Header()] = None) -> None:
    if x_data_api_key is None or not secrets.compare_digest(
        x_data_api_key.encode(), settings.DATA_API_KEY.encode()
    ):
        raise HTTPException(status_code=401, detail="Invalid data API key")


app = FastAPI(title="flwn-data-engine", lifespan=lifespan)
WorkspaceId = Annotated[str, Path(min_length=1, max_length=200, pattern=r"^\S+$")]


def steward(workspace_id: WorkspaceId) -> MemorySteward:
    return MemorySteward(workspace_id)


Memory = Annotated[MemorySteward, Depends(steward)]
NonBlank = Annotated[str, Field(min_length=1, max_length=100000, pattern=r"\S")]


class RememberRequest(BaseModel):
    text: NonBlank
    source: NonBlank
    agent: NonBlank


class RecallRequest(BaseModel):
    query: NonBlank
    agent: NonBlank
    limit: int = Field(default=5, ge=1, le=100)


class CleanupRequest(BaseModel):
    retention_days: int = Field(default=30, ge=1, le=36500)


class RememberResponse(BaseModel):
    point_id: str


class CleanupResponse(BaseModel):
    deleted: int


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz", dependencies=[Depends(authenticate)])
def readyz(response: Response) -> dict[str, bool]:
    status = health_check()
    if not all(status.values()):
        response.status_code = 503
    return status


@app.post("/workspaces/{workspace_id}/memories", dependencies=[Depends(authenticate)])
def remember(request: RememberRequest, memory: Memory) -> RememberResponse:
    return RememberResponse(point_id=memory.remember(**request.model_dump()))


@app.post(
    "/workspaces/{workspace_id}/memories/recall", dependencies=[Depends(authenticate)]
)
def recall(request: RecallRequest, memory: Memory) -> list[RankedResult]:
    return memory.recall(**request.model_dump())


@app.delete(
    "/workspaces/{workspace_id}/memories/{point_id}",
    status_code=204,
    dependencies=[Depends(authenticate)],
)
def forget(point_id: UUID, memory: Memory) -> Response:
    memory.forget(str(point_id))
    return Response(status_code=204)


@app.post(
    "/workspaces/{workspace_id}/memories/cleanup", dependencies=[Depends(authenticate)]
)
def cleanup(request: CleanupRequest, memory: Memory) -> CleanupResponse:
    return CleanupResponse(deleted=memory.run_cleanup(**request.model_dump()))


@app.post(
    "/workspaces/{workspace_id}/sprint-completed", dependencies=[Depends(authenticate)]
)
def sprint_completed(request: CleanupRequest, memory: Memory) -> CleanupResponse:
    return CleanupResponse(deleted=memory.on_sprint_completed(**request.model_dump()))
