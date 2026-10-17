import uuid
from datetime import datetime

from pydantic import Field

from db.enums import MemberRole
from schemas.common import UNSET, Description, Name, RequestModel, TimestampedRead


class CreatorMember(RequestModel):
    role: MemberRole = MemberRole.OWNER


class OrganizationCreate(RequestModel):
    name: Name
    description: Description = None
    created_by_id: uuid.UUID  # a User id
    creator_member: CreatorMember = Field(default_factory=CreatorMember)


class OrganizationUpdate(RequestModel):
    name: Name = UNSET
    description: Description = None
    archived: bool = UNSET


class OrganizationRead(TimestampedRead):
    created_by_id: uuid.UUID
    name: str
    description: str | None
    archived_at: datetime | None
