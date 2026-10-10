"""Request and response schemas for projects, work items, and tasks."""

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.identity_schemas import NonBlank

ProjectKey = Annotated[str, Field(min_length=1, max_length=10, pattern=r"^[A-Z][A-Z0-9]*$")]
PRIORITIES = {"none", "low", "medium", "high", "urgent"}
WORK_STATUSES = {"backlog", "todo", "in_progress", "in_review", "done", "canceled"}


class ProjectCreate(BaseModel):
    key: ProjectKey
    name: NonBlank
    description: Annotated[str, Field(max_length=10000)] | None = None
    status: str = "planned"
    team_id: UUID | None = None
    lead_id: UUID | None = None
    start_date: date | None = None
    target_date: date | None = None
    icon: Annotated[str, Field(max_length=100)] | None = None
    color: Annotated[str, Field(max_length=30)] | None = None

    @model_validator(mode="after")
    def validate_project(self):
        if self.status not in {
            "backlog",
            "planned",
            "in_progress",
            "paused",
            "completed",
            "canceled",
        }:
            raise ValueError("invalid project status")
        if self.start_date and self.target_date and self.target_date < self.start_date:
            raise ValueError("target_date cannot be before start_date")
        return self


class ProjectUpdate(BaseModel):
    key: ProjectKey | None = None
    name: NonBlank | None = None
    description: Annotated[str, Field(max_length=10000)] | None = None
    status: str | None = None
    team_id: UUID | None = None
    lead_id: UUID | None = None
    start_date: date | None = None
    target_date: date | None = None
    icon: Annotated[str, Field(max_length=100)] | None = None
    color: Annotated[str, Field(max_length=30)] | None = None

    @model_validator(mode="after")
    def validate_project_update(self):
        if not self.model_fields_set:
            raise ValueError("at least one field must be provided")
        if self.status is not None and self.status not in {
            "backlog",
            "planned",
            "in_progress",
            "paused",
            "completed",
            "canceled",
        }:
            raise ValueError("invalid project status")
        return self


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    team_id: UUID | None
    key: str
    name: str
    description: str | None
    status: str
    lead_id: UUID | None
    start_date: date | None
    target_date: date | None
    icon: str | None
    color: str | None
    task_counter: int
    created_at: datetime
    updated_at: datetime


class ProjectPage(BaseModel):
    items: list[ProjectOut]
    limit: int
    offset: int


class WorkItemCreate(BaseModel):
    title: NonBlank
    description: Annotated[str, Field(max_length=10000)] | None = None
    type: str = "FEATURE"
    priority: str = "none"
    status: str = "backlog"

    @model_validator(mode="after")
    def validate_work_item(self):
        if self.type not in {"FEATURE", "STORY", "BUG"}:
            raise ValueError("invalid work item type")
        if self.priority not in PRIORITIES:
            raise ValueError("invalid priority")
        if self.status not in WORK_STATUSES:
            raise ValueError("invalid status")
        return self


class WorkItemUpdate(BaseModel):
    title: NonBlank | None = None
    description: Annotated[str, Field(max_length=10000)] | None = None
    type: str | None = None
    priority: str | None = None
    status: str | None = None

    @model_validator(mode="after")
    def validate_work_item_update(self):
        if not self.model_fields_set:
            raise ValueError("at least one field must be provided")
        if self.type is not None and self.type not in {"FEATURE", "STORY", "BUG"}:
            raise ValueError("invalid work item type")
        if self.priority is not None and self.priority not in PRIORITIES:
            raise ValueError("invalid priority")
        if self.status is not None and self.status not in WORK_STATUSES:
            raise ValueError("invalid status")
        return self


class WorkItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    project_id: UUID
    backlog_id: UUID
    title: str
    description: str | None
    type: str
    priority: str
    status: str
    created_at: datetime
    updated_at: datetime


class WorkItemPage(BaseModel):
    items: list[WorkItemOut]
    limit: int
    offset: int


class TaskCreate(BaseModel):
    work_item_id: UUID
    title: NonBlank
    description: Annotated[str, Field(max_length=10000)] | None = None
    requirements: Annotated[str, Field(max_length=10000)] | None = None
    acceptance_criteria: Annotated[str, Field(max_length=10000)] | None = None
    priority: str = "none"
    status: str = "backlog"
    estimate: Annotated[int, Field(ge=0, le=32767)] | None = None
    due_date: date | None = None
    sprint_id: UUID | None = None
    milestone_id: UUID | None = None
    parent_task_id: UUID | None = None
    assignee_id: UUID | None = None
    sort_order: float = 0

    @model_validator(mode="after")
    def validate_task(self):
        if self.priority not in PRIORITIES:
            raise ValueError("invalid priority")
        if self.status not in WORK_STATUSES:
            raise ValueError("invalid status")
        return self


class TaskUpdate(BaseModel):
    work_item_id: UUID | None = None
    title: NonBlank | None = None
    description: Annotated[str, Field(max_length=10000)] | None = None
    requirements: Annotated[str, Field(max_length=10000)] | None = None
    acceptance_criteria: Annotated[str, Field(max_length=10000)] | None = None
    priority: str | None = None
    status: str | None = None
    estimate: Annotated[int, Field(ge=0, le=32767)] | None = None
    due_date: date | None = None
    sprint_id: UUID | None = None
    milestone_id: UUID | None = None
    parent_task_id: UUID | None = None
    assignee_id: UUID | None = None
    sort_order: float | None = None

    @model_validator(mode="after")
    def validate_task_update(self):
        if not self.model_fields_set:
            raise ValueError("at least one field must be provided")
        if self.priority is not None and self.priority not in PRIORITIES:
            raise ValueError("invalid priority")
        if self.status is not None and self.status not in WORK_STATUSES:
            raise ValueError("invalid status")
        return self


class TaskOut(BaseModel):
    id: UUID
    workspace_id: UUID
    project_id: UUID
    work_item_id: UUID
    sprint_id: UUID | None
    milestone_id: UUID | None
    parent_task_id: UUID | None
    number: int
    identifier: str
    title: str
    description: str | None
    requirements: str | None
    acceptance_criteria: str | None
    priority: str
    status: str
    estimate: int | None
    due_date: date | None
    started_at: datetime | None
    completed_at: datetime | None
    sort_order: float
    assignee_id: UUID | None
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime


class TaskPage(BaseModel):
    items: list[TaskOut]
    limit: int
    offset: int
