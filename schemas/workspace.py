import uuid
from datetime import datetime

from schemas.common import UNSET, Description, Name, RequestModel, TimestampedRead


class WorkspaceCreate(RequestModel):
    name: Name
    description: Description = None
    created_by_id: uuid.UUID


class WorkspaceUpdate(RequestModel):
    name: Name = UNSET
    description: Description = None
    archived: bool = UNSET


class WorkspaceRead(TimestampedRead):
    organization_id: uuid.UUID
    created_by_id: uuid.UUID
    name: str
    description: str | None
    archived_at: datetime | None
