from schemas.common import UNSET, Email, Name, RequestModel, TimestampedRead


class UserCreate(RequestModel):
    name: Name
    email: Email


class UserUpdate(RequestModel):
    name: Name = UNSET
    email: Email = UNSET


class UserRead(TimestampedRead):
    name: str
    email: str
