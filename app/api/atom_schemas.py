"""Validated atom control-plane requests; no arbitrary column updates."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

NonBlank = Annotated[str, Field(min_length=1, max_length=20000, pattern=r"\S")]
Money = Annotated[Decimal, Field(ge=0, allow_inf_nan=False, max_digits=12, decimal_places=6)]


class AtomBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateAtom(AtomBody):
    name: NonBlank
    description: str | None = None
    kind: Literal["workspace", "personal"] = "workspace"
    owner_member_id: UUID | None = None
    model_tier: Literal["small", "medium"] = "small"
    max_runs_per_day: int = Field(ge=0)
    max_cost_per_day: Money
    max_actions_per_day: int = Field(ge=0)


class ConfigureAtom(AtomBody):
    name: NonBlank | None = None
    description: str | None = None
    model_tier: Literal["small", "medium"] | None = None
    status: Literal["draft", "paused", "killed"] | None = None
    max_runs_per_day: int | None = Field(default=None, ge=0)
    max_cost_per_day: Money | None = None
    max_actions_per_day: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def non_null_configuration(self):
        for field in self.model_fields_set - {"description"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class AtomVersionBody(AtomBody):
    instructions: NonBlank
    policy: dict = Field(default_factory=dict)
    tools: list = Field(default_factory=list, max_length=200)


class SkillAttachment(AtomBody):
    skill_id: UUID
    skill_version: int = Field(ge=1)
    catalog: bool = False


class AttachmentState(AtomBody):
    enabled: bool


class ActivateVersion(AtomBody):
    version_id: UUID


class GrantBody(AtomBody):
    resource_type: Literal[
        "project", "team", "collection", "channel", "folder", "memory", "meeting", "connection"
    ]
    resource_id: UUID | None = None
    level: Literal["summary", "read", "write"]
    constraints: dict = Field(default_factory=dict)


class ScheduleBody(AtomBody):
    cron: NonBlank | None = None
    interval_minutes: int | None = Field(default=None, ge=5)
    timezone: str = Field(default="UTC", min_length=1, max_length=100)
    enabled: bool = True
    next_run_at: datetime | None = None

    @model_validator(mode="after")
    def one_cadence(self):
        if (self.cron is None) == (self.interval_minutes is None):
            raise ValueError("provide exactly one of cron and interval_minutes")
        if self.next_run_at is not None and self.next_run_at.utcoffset() is None:
            raise ValueError("next_run_at must include a timezone")
        return self


class ReleaseStaleRuns(AtomBody):
    older_than_seconds: int = Field(strict=True, ge=900, le=31536000)


class StartRun(AtomBody):
    schedule_id: UUID
    scheduled_for: datetime
    idempotency_key: Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
    estimated_cost: Money = Decimal("0")
    estimated_actions: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def aware_slot(self):
        if self.scheduled_for.utcoffset() is None:
            raise ValueError("scheduled_for must include a timezone")
        return self


class ConnectionBody(AtomBody):
    toolkit: Annotated[str, Field(min_length=1, max_length=200, pattern=r"^\S+$")]
    composio_user_id: NonBlank
    composio_account_ref: NonBlank | None = None
    toolkit_version: Annotated[str, Field(min_length=1, max_length=200, pattern=r"^\S+$")]
    permission_ceiling: Literal["read", "draft", "write", "destructive"]
    allowed_tools: list[Annotated[str, Field(min_length=1, max_length=200, pattern=r"^\S+$")]] = (
        Field(default_factory=list, max_length=500)
    )
    status: Literal["active", "expired", "revoked", "unknown"] = "unknown"

    @model_validator(mode="after")
    def pinned_version(self):
        if self.toolkit_version.lower() == "latest":
            raise ValueError("toolkit_version must be pinned, not latest")
        if self.status == "active" and not self.composio_account_ref:
            raise ValueError("active connection requires composio_account_ref")
        return self
