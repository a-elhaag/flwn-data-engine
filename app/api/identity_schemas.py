"""Identity and workspace request and response bodies."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

NonBlank = Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
Slug = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]


class WorkspaceCreate(BaseModel):
    name: NonBlank
    slug: Slug
    description: Annotated[str, Field(max_length=2000)] | None = None
    settings: dict = Field(default_factory=dict)


class WorkspaceUpdate(BaseModel):
    name: NonBlank | None = None
    slug: Slug | None = None
    description: Annotated[str, Field(max_length=2000)] | None = None
    settings: dict | None = None

    @model_validator(mode="after")
    def has_updates(self):
        if not self.model_fields_set:
            raise ValueError("at least one field must be provided")
        for field in self.model_fields_set - {"description"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class WorkspaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    description: str | None
    settings: dict
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime


class WorkspacePage(BaseModel):
    items: list[WorkspaceOut]
    limit: int
    offset: int


class MemberCreate(BaseModel):
    type: str
    role: str = "member"
    status: str = "active"
    name: NonBlank | None = None
    email: (
        Annotated[str, Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")]
        | None
    ) = None
    agent_kind: str | None = None
    avatar_url: Annotated[str, Field(max_length=2000)] | None = None
    settings: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_identity(self):
        if self.type == "HUMAN":
            if not self.email or not self.name:
                raise ValueError("human members require email and name")
            if self.agent_kind or self.avatar_url:
                raise ValueError("human profile fields belong to the user account")
        elif self.type == "AI":
            if not self.name or not self.agent_kind:
                raise ValueError("AI members require name and agent_kind")
            if self.email:
                raise ValueError("AI members cannot have an email")
        else:
            raise ValueError("type must be HUMAN or AI")
        if self.role not in {"owner", "admin", "member", "viewer"}:
            raise ValueError("invalid member role")
        if self.status not in {"active", "suspended"}:
            raise ValueError("invalid member status")
        return self


class MemberUpdate(BaseModel):
    role: str | None = None
    status: str | None = None
    name: NonBlank | None = None
    email: (
        Annotated[str, Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")]
        | None
    ) = None
    avatar_url: Annotated[str, Field(max_length=2000)] | None = None
    settings: dict | None = None

    @model_validator(mode="after")
    def validate_update(self):
        if not self.model_fields_set:
            raise ValueError("at least one field must be provided")
        for field in self.model_fields_set - {"avatar_url"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        if self.role is not None and self.role not in {"owner", "admin", "member", "viewer"}:
            raise ValueError("invalid member role")
        if self.status is not None and self.status not in {"active", "suspended"}:
            raise ValueError("invalid member status")
        return self


class MemberOut(BaseModel):
    id: UUID
    workspace_id: UUID
    user_id: UUID | None
    type: str
    name: str | None
    email: str | None
    role: str
    status: str
    agent_kind: str | None
    avatar_url: str | None
    settings: dict
    created_at: datetime
    updated_at: datetime


class TeamCreate(BaseModel):
    key: Annotated[str, Field(min_length=1, max_length=30, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")]
    name: NonBlank
    description: Annotated[str, Field(max_length=2000)] | None = None
    icon: Annotated[str, Field(max_length=100)] | None = None
    color: Annotated[str, Field(max_length=30)] | None = None
    visibility: str = "workspace"

    @model_validator(mode="after")
    def validate_visibility(self):
        if self.visibility not in {"workspace", "private"}:
            raise ValueError("visibility must be workspace or private")
        return self


class TeamUpdate(BaseModel):
    key: (
        Annotated[str, Field(min_length=1, max_length=30, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")]
        | None
    ) = None
    name: NonBlank | None = None
    description: Annotated[str, Field(max_length=2000)] | None = None
    icon: Annotated[str, Field(max_length=100)] | None = None
    color: Annotated[str, Field(max_length=30)] | None = None
    visibility: str | None = None

    @model_validator(mode="after")
    def validate_update(self):
        if not self.model_fields_set:
            raise ValueError("at least one field must be provided")
        for field in self.model_fields_set - {"description", "icon", "color"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        if self.visibility is not None and self.visibility not in {"workspace", "private"}:
            raise ValueError("visibility must be workspace or private")
        return self


class TeamOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    key: str
    name: str
    description: str | None
    icon: str | None
    color: str | None
    visibility: str
    created_at: datetime
    updated_at: datetime


class MemberPage(BaseModel):
    items: list[MemberOut]
    limit: int
    offset: int


class TeamPage(BaseModel):
    items: list[TeamOut]
    limit: int
    offset: int


class TeamMembersUpdate(BaseModel):
    member_ids: list[UUID] = Field(max_length=500)
