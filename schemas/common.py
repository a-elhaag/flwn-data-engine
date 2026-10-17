import uuid
from datetime import datetime, timezone
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    EmailStr,
    StringConstraints,
)
from pydantic.alias_generators import to_camel

# Default for Update (PATCH) fields that are NOT NULL in the DB. "Not sent" stays
# out of model_fields_set; an explicit null fails validation because the declared
# type does not allow None. Create schemas use real defaults instead.
UNSET: Any = None


class RequestModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        alias_generator=to_camel,
        populate_by_name=True,
    )


class ResponseModel(BaseModel):
    """Serialize with model_dump(by_alias=True) / by_alias=True so keys are camelCase."""

    model_config = ConfigDict(
        from_attributes=True, alias_generator=to_camel, populate_by_name=True
    )


class TimestampedRead(ResponseModel):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


def _blank_to_none(value: str) -> str | None:
    return value or None


def _lower_email(value: str) -> str:
    if len(value) > 254:
        raise ValueError("email must be at most 254 characters")
    return value.lower()


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Description = (
    Annotated[
        str,
        StringConstraints(strip_whitespace=True, max_length=5000),
        AfterValidator(_blank_to_none),
    ]
    | None
)
LongText = (
    Annotated[
        str,
        StringConstraints(strip_whitespace=True, max_length=10000),
        AfterValidator(_blank_to_none),
    ]
    | None
)
Email = Annotated[EmailStr, AfterValidator(_lower_email)]
ProjectKey = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2,10}$")]
UtcDatetime = Annotated[AwareDatetime, AfterValidator(_to_utc)]
