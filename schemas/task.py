import uuid
from datetime import datetime

from pydantic import model_validator

from db.enums import Priority, TaskStatus
from schemas.common import (
    UNSET,
    Description,
    LongText,
    RequestModel,
    TimestampedRead,
    Title,
    UtcDatetime,
)


class TaskCreate(RequestModel):
    title: Title
    description: Description = None
    requirements: LongText = None
    acceptance_criteria: LongText = None
    priority: Priority = Priority.MEDIUM
    status: TaskStatus = TaskStatus.TODO
    due_date: UtcDatetime | None = None
    completed_at: UtcDatetime | None = None
    work_item_id: uuid.UUID | None = None
    sprint_id: uuid.UUID | None = None
    assignee_id: uuid.UUID | None = None
    created_by_id: uuid.UUID

    @model_validator(mode="after")
    def done_iff_completed(self):
        if (self.status == TaskStatus.DONE) != (self.completed_at is not None):
            raise ValueError("status is DONE if and only if completedAt is set")
        return self


class TaskUpdate(RequestModel):
    title: Title = UNSET
    description: Description = None
    requirements: LongText = None
    acceptance_criteria: LongText = None
    priority: Priority = UNSET
    status: TaskStatus = UNSET
    due_date: UtcDatetime | None = None
    completed_at: UtcDatetime | None = None
    work_item_id: uuid.UUID | None = None
    sprint_id: uuid.UUID | None = None
    assignee_id: uuid.UUID | None = None


class TaskRead(TimestampedRead):
    organization_id: uuid.UUID
    project_id: uuid.UUID
    number: int
    work_item_id: uuid.UUID | None
    sprint_id: uuid.UUID | None
    assignee_id: uuid.UUID | None
    created_by_id: uuid.UUID
    title: str
    description: str | None
    requirements: str | None
    acceptance_criteria: str | None
    priority: Priority
    status: TaskStatus
    due_date: datetime | None
    completed_at: datetime | None
