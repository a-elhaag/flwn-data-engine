import uuid
from datetime import datetime

from db.enums import TeamMemberRole
from schemas.common import (
    UNSET,
    Description,
    Name,
    RequestModel,
    ResponseModel,
    TimestampedRead,
)


class TeamCreate(RequestModel):
    name: Name
    description: Description = None
    created_by_id: uuid.UUID


class TeamUpdate(RequestModel):
    name: Name = UNSET
    description: Description = None
    archived: bool = UNSET


class TeamRead(TimestampedRead):
    organization_id: uuid.UUID
    workspace_id: uuid.UUID
    created_by_id: uuid.UUID
    name: str
    description: str | None
    archived_at: datetime | None


class TeamMemberCreate(RequestModel):
    member_id: uuid.UUID
    role: TeamMemberRole = TeamMemberRole.MEMBER


class TeamMemberUpdate(RequestModel):
    role: TeamMemberRole = UNSET


class TeamMemberRead(ResponseModel):
    team_id: uuid.UUID
    member_id: uuid.UUID
    organization_id: uuid.UUID
    role: TeamMemberRole
    joined_at: datetime
