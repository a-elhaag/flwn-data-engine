"""Request and response bodies for the REST API."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

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
    member_id: UUID | None = None  # the member the token acts as; recommended
    atom_id: UUID | None = None
    run_id: UUID | None = None
    subject: NonBlank = "agent"
    scopes: list[str] = Field(default_factory=lambda: list(DEFAULT_SCOPES))
    ttl_seconds: int = Field(default=3600, ge=1)


class TokenResponse(BaseModel):
    token: str
    expires_at: int


class FlagConflictRequest(BaseModel):
    decision_id: UUID  # the decision memory's id, from recall
    proposal: Annotated[str, Field(min_length=1, max_length=20000, pattern=r"\S")]
    explanation: Annotated[str, Field(min_length=1, max_length=5000, pattern=r"\S")]
    similarity: float | None = Field(default=None, ge=0, le=1)


class ResolveConflictRequest(BaseModel):
    status: Literal["accepted", "dismissed", "resolved"]
    note: Annotated[str, Field(max_length=5000)] | None = None


FileKind = Literal[
    "document", "pdf", "image", "voice_note", "audio", "video", "recording", "report", "other"
]


class SearchFilesRequest(BaseModel):
    query: Annotated[str, Field(min_length=1, max_length=2000, pattern=r"\S")]
    limit: int = Field(default=5, ge=1, le=50)
    kind: FileKind | None = None
    project_id: UUID | None = None


class StartUploadRequest(BaseModel):
    kind: FileKind
    name: Annotated[str, Field(min_length=1, max_length=255, pattern=r"\S")]
    content_type: Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
    size_bytes: int = Field(ge=1)
    source: Literal["workspace", "chat", "meeting", "agent", "import"] = "workspace"
    project_id: UUID | None = None
    folder_id: UUID | None = None
    team_id: UUID | None = None


class CreateMeetingRequest(BaseModel):
    title: Annotated[str, Field(min_length=1, max_length=300, pattern=r"\S")]
    participants: list[UUID] = Field(default_factory=list, max_length=200)
    team_id: UUID | None = None
    project_id: UUID | None = None
    channel_id: UUID | None = None
    scheduled_at: datetime | None = None
    host_id: UUID | None = None  # only the trusted backend, acting for no one, may name the host


class UpdateMeetingRequest(BaseModel):
    title: Annotated[str, Field(min_length=1, max_length=300, pattern=r"\S")] | None = None
    status: Literal["live", "ended", "canceled"] | None = None
    scheduled_at: datetime | None = None


class InviteRequest(BaseModel):
    member_ids: list[UUID] = Field(min_length=1, max_length=200)


class ConsentRequest(BaseModel):
    status: Literal["granted", "declined"]


class StartRecordingRequest(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=255, pattern=r"\S")]
    content_type: Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
    size_bytes: int = Field(ge=1)


class TranscriptSegmentIn(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    text: Annotated[str, Field(min_length=1, max_length=5000, pattern=r"\S")]
    speaker_label: Annotated[str, Field(max_length=100)] | None = None
    speaker_member_id: UUID | None = None

    @model_validator(mode="after")
    def _ordered(self):
        if self.end_ms < self.start_ms:
            raise ValueError("end_ms must not be before start_ms")
        return self


class IngestTranscriptRequest(BaseModel):
    segments: list[TranscriptSegmentIn] = Field(min_length=1, max_length=20000)
    language: Annotated[str, Field(max_length=20)] | None = None
