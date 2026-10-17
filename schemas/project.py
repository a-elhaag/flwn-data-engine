import uuid
from datetime import datetime

from db.enums import ProjectStatus
from schemas.common import (
    UNSET,
    Description,
    Name,
    ProjectKey,
    RequestModel,
    ResponseModel,
    TimestampedRead,
)


class ProjectCreate(RequestModel):
    key: ProjectKey
    name: Name
    description: Description = None
    status: ProjectStatus = ProjectStatus.ACTIVE
    created_by_id: uuid.UUID


class ProjectUpdate(RequestModel):
    name: Name = UNSET
    description: Description = None
    status: ProjectStatus = UNSET
    archived: bool = UNSET


class ProjectRead(TimestampedRead):
    organization_id: uuid.UUID
    workspace_id: uuid.UUID
    created_by_id: uuid.UUID
    key: str
    name: str
    description: str | None
    status: ProjectStatus
    archived_at: datetime | None


class ProjectTeamCreate(RequestModel):
    team_id: uuid.UUID


class ProjectTeamRead(ResponseModel):
    project_id: uuid.UUID
    team_id: uuid.UUID
    organization_id: uuid.UUID
    joined_at: datetime
