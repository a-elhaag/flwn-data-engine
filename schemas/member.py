import uuid

from pydantic import AliasChoices, Field, model_validator

from db.enums import MemberRole, MemberStatus, MemberType
from schemas.common import UNSET, Email, Name, RequestModel, TimestampedRead


class MemberCreate(RequestModel):
    type: MemberType
    user_id: uuid.UUID | None = None
    name: Name | None = None
    email: Email | None = None
    role: MemberRole = UNSET
    status: MemberStatus = UNSET
    workspace_id: uuid.UUID | None = None
    created_by_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def human_or_ai(self):
        if self.type == MemberType.HUMAN:
            if self.user_id is None:
                raise ValueError("userId is required for a HUMAN member")
            if self.name is not None or self.email is not None:
                raise ValueError("name and email must be absent for a HUMAN member")
        else:
            if self.user_id is not None:
                raise ValueError("userId must be absent for an AI member")
            if self.name is None:
                raise ValueError("name is required for an AI member")
        return self


class MemberUpdate(RequestModel):
    role: MemberRole = UNSET
    status: MemberStatus = UNSET
    workspace_id: uuid.UUID | None = None
    name: Name | None = None
    email: Email | None = None


class MemberRead(TimestampedRead):
    organization_id: uuid.UUID
    user_id: uuid.UUID | None
    workspace_id: uuid.UUID | None
    created_by_id: uuid.UUID | None
    type: MemberType
    # Filled from Member.display_name / display_email (user's for HUMAN).
    name: str | None = Field(
        validation_alias=AliasChoices("display_name", "name"), serialization_alias="name"
    )
    email: str | None = Field(
        validation_alias=AliasChoices("display_email", "email"),
        serialization_alias="email",
    )
    role: MemberRole
    status: MemberStatus
