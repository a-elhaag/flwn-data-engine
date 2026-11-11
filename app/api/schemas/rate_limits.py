"""Rate-limit bodies. The key is a hash the backend computed; the engine never sees the raw key."""

from pydantic import BaseModel, Field


class RateKey(BaseModel):
    bucket: str = Field(min_length=1, max_length=40, pattern=r"^[a-z_]+$")
    key_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class RateWindow(RateKey):
    window_seconds: int = Field(ge=1, le=86400)


class RateCount(BaseModel):
    count: int
