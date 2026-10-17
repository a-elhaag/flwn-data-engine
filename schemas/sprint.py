import uuid
from datetime import date

from pydantic import model_validator

from db.enums import SprintStatus
from schemas.common import UNSET, Description, Name, RequestModel, TimestampedRead


class SprintCreate(RequestModel):
    name: Name
    goal: Description = None
    status: SprintStatus = SprintStatus.PLANNED
    start_date: date | None = None
    end_date: date | None = None
    created_by_id: uuid.UUID

    @model_validator(mode="after")
    def dates_in_order(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("endDate must not be before startDate")
        return self


class SprintUpdate(RequestModel):
    name: Name = UNSET
    goal: Description = None
    status: SprintStatus = UNSET
    start_date: date | None = None
    end_date: date | None = None


class SprintRead(TimestampedRead):
    organization_id: uuid.UUID
    project_id: uuid.UUID
    team_id: uuid.UUID
    created_by_id: uuid.UUID
    name: str
    goal: str | None
    status: SprintStatus
    start_date: date | None
    end_date: date | None
