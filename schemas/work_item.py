import uuid

from db.enums import Priority, WorkItemStatus, WorkItemType
from schemas.common import UNSET, Description, RequestModel, TimestampedRead, Title


class WorkItemCreate(RequestModel):
    title: Title
    description: Description = None
    type: WorkItemType
    priority: Priority = UNSET
    status: WorkItemStatus = UNSET
    created_by_id: uuid.UUID


class WorkItemUpdate(RequestModel):
    title: Title = UNSET
    description: Description = None
    type: WorkItemType = UNSET
    priority: Priority = UNSET
    status: WorkItemStatus = UNSET


class WorkItemRead(TimestampedRead):
    organization_id: uuid.UUID
    project_id: uuid.UUID
    created_by_id: uuid.UUID
    title: str
    description: str | None
    type: WorkItemType
    priority: Priority
    status: WorkItemStatus
