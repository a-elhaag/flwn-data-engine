"""AI run bodies. The state is the AI engine's graph state, stored as given; the engine never reads it."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class AiRunIn(BaseModel):
    workspace_id: UUID
    project_id: UUID
    agent_member_id: UUID
    requester_member_id: UUID
    stage: Literal["plan", "review"]
    state: dict[str, Any]


class AiRunOut(BaseModel):
    task_id: UUID
    workspace_id: UUID
    project_id: UUID
    agent_member_id: UUID
    requester_member_id: UUID
    stage: str
    state: dict[str, Any]
    review_status: str
    review_reason: str | None
    updated_at: datetime


class ClaimIn(BaseModel):
    expected_stage: Literal["plan", "review"]


class ReviewIn(BaseModel):
    status: Literal["approved", "rejected"]
    reason: str | None = Field(default=None, max_length=2000)
