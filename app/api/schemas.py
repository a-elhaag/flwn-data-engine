"""Request and response bodies for the REST API."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.api import tokens

NonBlank = Annotated[str, Field(min_length=1, max_length=100000, pattern=r"\S")]


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


# A token gets only what is asked for. Without a request it can read, and nothing else.
DEFAULT_SCOPES = sorted({tokens.SCOPE_READ, tokens.SCOPE_FILES_READ})


class TokenRequest(BaseModel):
    workspace_id: UUID
    subject: NonBlank = "agent"
    scopes: list[str] = Field(default_factory=lambda: list(DEFAULT_SCOPES))
    ttl_seconds: int = Field(default=3600, ge=1)


class TokenResponse(BaseModel):
    token: str
    expires_at: int


FileKind = Literal[
    "document", "pdf", "image", "voice_note", "audio", "video", "recording", "report", "other"
]


class StartUploadRequest(BaseModel):
    kind: FileKind
    name: Annotated[str, Field(min_length=1, max_length=255, pattern=r"\S")]
    content_type: Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
    size_bytes: int = Field(ge=1)
    source: Literal["workspace", "chat", "meeting", "agent", "import"] = "workspace"
    project_id: UUID | None = None
    folder_id: UUID | None = None
    team_id: UUID | None = None
