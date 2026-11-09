"""Identity and workspace request and response bodies."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from app.db.base import MEMBER_ROLES, MEMBER_STATUSES
from app.domain.identity.email import canonical_email

NonBlank = Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
Slug = Annotated[
    str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$")
]
# Stored and compared in canonical form only (app/identity/email.py, the backend's definition).
Email = Annotated[
    str,
    Field(min_length=3, max_length=320, examples=["ada@example.com"]),
    AfterValidator(canonical_email),
]


class WorkspaceCreate(BaseModel):
    name: NonBlank
    slug: Slug
    owner_user_id: UUID
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
    has_more: bool = False


class MemberCreate(BaseModel):
    type: str
    role: str = "member"
    status: str = "active"
    name: NonBlank | None = None
    email: Email | None = None
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
            if self.role == "owner":
                raise ValueError("AI members cannot be owners")
        else:
            raise ValueError("type must be HUMAN or AI")
        if self.role not in MEMBER_ROLES:
            raise ValueError("invalid member role")
        if self.status not in MEMBER_STATUSES:
            raise ValueError("invalid member status")
        return self


class MemberUpdate(BaseModel):
    role: str | None = None
    status: str | None = None
    name: NonBlank | None = None
    email: Email | None = None
    avatar_url: Annotated[str, Field(max_length=2000)] | None = None
    settings: dict | None = None

    @model_validator(mode="after")
    def validate_update(self):
        if not self.model_fields_set:
            raise ValueError("at least one field must be provided")
        for field in self.model_fields_set - {"avatar_url"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        if self.role is not None and self.role not in MEMBER_ROLES:
            raise ValueError("invalid member role")
        if self.status is not None and self.status not in MEMBER_STATUSES:
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


class TeamUpdate(BaseModel):
    key: (
        Annotated[str, Field(min_length=1, max_length=30, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")]
        | None
    ) = None
    name: NonBlank | None = None
    description: Annotated[str, Field(max_length=2000)] | None = None
    icon: Annotated[str, Field(max_length=100)] | None = None
    color: Annotated[str, Field(max_length=30)] | None = None

    @model_validator(mode="after")
    def validate_update(self):
        if not self.model_fields_set:
            raise ValueError("at least one field must be provided")
        for field in self.model_fields_set - {"description", "icon", "color"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
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
    created_at: datetime
    updated_at: datetime


class MemberPage(BaseModel):
    items: list[MemberOut]
    limit: int
    offset: int
    has_more: bool = False


class TeamPage(BaseModel):
    items: list[TeamOut]
    limit: int
    offset: int
    has_more: bool = False


class TeamMembersUpdate(BaseModel):
    member_ids: list[UUID] = Field(max_length=500)


class UserCreate(BaseModel):
    email: Email
    name: NonBlank
    avatar_url: Annotated[str, Field(max_length=2000)] | None = None
    settings: dict = Field(default_factory=dict)


class UserUpdate(BaseModel):
    email: Email | None = None
    name: NonBlank | None = None
    avatar_url: Annotated[str, Field(max_length=2000)] | None = None
    settings: dict | None = None

    @model_validator(mode="after")
    def has_updates(self):
        if not self.model_fields_set:
            raise ValueError("at least one field must be provided")
        for field in self.model_fields_set - {"avatar_url"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    name: str
    avatar_url: str | None
    settings: dict
    created_at: datetime
    updated_at: datetime


class UserPage(BaseModel):
    items: list[UserOut]
    limit: int
    offset: int
    has_more: bool = False


class MembershipOut(BaseModel):
    """One of a user's memberships, with the workspace it belongs to."""

    member_id: UUID
    workspace_id: UUID
    workspace_name: str
    workspace_slug: str
    user_id: UUID
    type: str
    role: str
    status: str


class MembershipList(BaseModel):
    items: list[MembershipOut]
    total: int


class OwnershipTransfer(BaseModel):
    to_member_id: UUID
    demote_self_to: Literal["admin", "member"] | None = None


class OwnershipTransferOut(BaseModel):
    workspace_id: UUID
    from_member: MemberOut
    to_member: MemberOut
