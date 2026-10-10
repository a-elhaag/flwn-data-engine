"""Request and response schemas for comments, structured docs, and chat."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from app.api.identity_schemas import NonBlank
from app.db.models.collab import BLOCK_TYPES

CommentTargetType = Literal["task", "work_item", "project", "project_update", "sprint", "doc"]


class CommentCreate(BaseModel):
    target_type: CommentTargetType
    target_id: UUID
    block_id: UUID | None = None
    parent_comment_id: UUID | None = None
    body: NonBlank
    body_json: dict | None = None
    author_id: UUID | None = None

    @model_validator(mode="after")
    def validate_target(self):
        if self.block_id is not None and self.target_type != "doc":
            raise ValueError("block_id can only be used with a doc comment")
        return self


class CommentUpdate(BaseModel):
    body: NonBlank | None = None
    body_json: dict | None = None
    resolved: bool | None = None

    @model_validator(mode="after")
    def validate_update(self):
        if not self.model_fields_set:
            raise ValueError("at least one field must be provided")
        if "body" in self.model_fields_set and self.body is None:
            raise ValueError("body cannot be null")
        return self


class CommentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    task_id: UUID | None
    work_item_id: UUID | None
    project_id: UUID | None
    project_update_id: UUID | None
    sprint_id: UUID | None
    doc_id: UUID | None
    block_id: UUID | None
    parent_comment_id: UUID | None
    author_id: UUID | None
    body: str
    body_json: dict | None
    resolved_at: datetime | None
    resolved_by: UUID | None
    edited_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CommentPage(BaseModel):
    items: list[CommentOut]
    limit: int
    offset: int


class DocCreate(BaseModel):
    title: NonBlank = "Untitled"
    team_id: UUID | None = None
    project_id: UUID | None = None
    parent_doc_id: UUID | None = None
    collection_id: UUID | None = None
    icon: Annotated[str, Field(max_length=100)] | None = None
    cover_file_id: UUID | None = None
    properties: dict = Field(default_factory=dict)
    sort_order: float = 0
    is_template: bool = False
    is_locked: bool = False
    published_slug: (
        Annotated[
            str, Field(min_length=1, max_length=200, pattern=r"^[a-zA-Z0-9]+(?:-[a-zA-Z0-9]+)*$")
        ]
        | None
    ) = None


class DocUpdate(BaseModel):
    title: NonBlank | None = None
    team_id: UUID | None = None
    project_id: UUID | None = None
    parent_doc_id: UUID | None = None
    collection_id: UUID | None = None
    icon: Annotated[str, Field(max_length=100)] | None = None
    cover_file_id: UUID | None = None
    properties: dict | None = None
    sort_order: float | None = None
    is_template: bool | None = None
    is_locked: bool | None = None
    published_slug: (
        Annotated[
            str, Field(min_length=1, max_length=200, pattern=r"^[a-zA-Z0-9]+(?:-[a-zA-Z0-9]+)*$")
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def validate_update(self):
        if not self.model_fields_set:
            raise ValueError("at least one field must be provided")
        for field in self.model_fields_set - {
            "team_id",
            "project_id",
            "parent_doc_id",
            "collection_id",
            "icon",
            "cover_file_id",
            "properties",
            "published_slug",
        }:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class DocOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    team_id: UUID | None
    project_id: UUID | None
    parent_doc_id: UUID | None
    collection_id: UUID | None
    title: str
    icon: str | None
    cover_file_id: UUID | None
    properties: dict
    sort_order: float
    is_template: bool
    is_locked: bool
    published_slug: str | None
    created_by: UUID | None
    last_edited_by: UUID | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DocPage(BaseModel):
    items: list[DocOut]
    limit: int
    offset: int


class BlockCreate(BaseModel):
    type: str
    content: dict = Field(default_factory=dict)
    parent_block_id: UUID | None = None
    sort_order: float = 0

    @model_validator(mode="after")
    def validate_block_type(self):
        if self.type not in BLOCK_TYPES:
            raise ValueError("invalid block type")
        return self


class BlockUpdate(BaseModel):
    type: str | None = None
    content: dict | None = None
    parent_block_id: UUID | None = None
    sort_order: float | None = None

    @model_validator(mode="after")
    def validate_update(self):
        if not self.model_fields_set:
            raise ValueError("at least one field must be provided")
        if self.type is not None and self.type not in BLOCK_TYPES:
            raise ValueError("invalid block type")
        if "content" in self.model_fields_set and self.content is None:
            raise ValueError("content cannot be null")
        return self


class BlockOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    doc_id: UUID
    parent_block_id: UUID | None
    type: str
    content: dict
    sort_order: float
    created_by: UUID | None
    last_edited_by: UUID | None
    created_at: datetime
    updated_at: datetime


class BlockPage(BaseModel):
    items: list[BlockOut]
    limit: int
    offset: int


class ChannelCreate(BaseModel):
    type: str
    name: Annotated[str, Field(max_length=100)] | None = None
    topic: Annotated[str, Field(max_length=1000)] | None = None
    team_id: UUID | None = None
    project_id: UUID | None = None
    is_private: bool = False
    member_ids: list[UUID] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def validate_channel(self):
        if self.type not in {"team", "project", "group", "dm", "agent"}:
            raise ValueError("invalid channel type")
        if self.type == "team" and self.team_id is None:
            raise ValueError("team channels require team_id")
        if self.type == "project" and self.project_id is None:
            raise ValueError("project channels require project_id")
        if self.type != "team" and self.team_id is not None:
            raise ValueError("team_id is only valid for team channels")
        if self.type != "project" and self.project_id is not None:
            raise ValueError("project_id is only valid for project channels")
        return self


class ChannelUpdate(BaseModel):
    name: Annotated[str, Field(max_length=100)] | None = None
    topic: Annotated[str, Field(max_length=1000)] | None = None
    is_private: bool | None = None

    @model_validator(mode="after")
    def validate_update(self):
        if not self.model_fields_set:
            raise ValueError("at least one field must be provided")
        return self


class ChannelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    team_id: UUID | None
    project_id: UUID | None
    type: str
    name: str | None
    topic: str | None
    is_private: bool
    created_by: UUID | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ChannelPage(BaseModel):
    items: list[ChannelOut]
    limit: int
    offset: int


class ChannelMembersUpdate(BaseModel):
    member_ids: list[UUID] = Field(max_length=500)


class MessageCreate(BaseModel):
    body: Annotated[str, Field(min_length=1, max_length=50000)] | None = None
    body_json: dict | None = None
    kind: str = "text"
    parent_message_id: UUID | None = None
    author_id: UUID | None = None

    @model_validator(mode="after")
    def validate_message(self):
        if self.kind not in {"text", "voice_note", "image", "file", "system", "agent_report"}:
            raise ValueError("invalid message kind")
        if self.body is None and self.body_json is None:
            raise ValueError("body or body_json is required")
        return self


class MessageUpdate(BaseModel):
    body: Annotated[str, Field(min_length=1, max_length=50000)] | None = None
    body_json: dict | None = None
    pinned: bool | None = None

    @model_validator(mode="after")
    def validate_update(self):
        if not self.model_fields_set:
            raise ValueError("at least one field must be provided")
        if "body" in self.model_fields_set and self.body is None:
            raise ValueError("body cannot be null")
        return self


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    channel_id: UUID
    author_id: UUID | None
    parent_message_id: UUID | None
    kind: str
    body: str | None
    body_json: dict | None
    metadata: dict = Field(validation_alias=AliasChoices("metadata_", "metadata"))
    pinned_at: datetime | None
    edited_at: datetime | None
    deleted_at: datetime | None
    created_at: datetime


class MessagePage(BaseModel):
    items: list[MessageOut]
    limit: int
    offset: int
