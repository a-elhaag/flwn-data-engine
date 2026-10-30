"""Atom approval request, bootstrap and outcome bodies."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class ApprovalPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_slug: Annotated[str, Field(min_length=1, max_length=300)]
    arguments: dict
    connection_id: UUID


class CreateApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["plan", "pull_request", "deploy", "action", "atom_action"] = "atom_action"
    title: Annotated[str, Field(min_length=1, max_length=300)]
    payload: ApprovalPayload
    request_key: Annotated[str | None, Field(min_length=1, max_length=200)] = None
    assigned_to: UUID | None = None
    expires_at: AwareDatetime | None = None


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["approved", "rejected"]
    comment: Annotated[str | None, Field(max_length=4000)] = None


class ApprovalBootstrap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    estimated_cost: Annotated[str, Field(pattern=r"^\d+(\.\d{1,6})?$")] = "0"
    estimated_actions: Annotated[int, Field(ge=0, strict=True)] = 1


class ApprovalOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["succeeded", "failed", "unknown"]
    result: dict | None = None
