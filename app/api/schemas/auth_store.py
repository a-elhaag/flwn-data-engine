"""Auth store request and response bodies. Hashes only: the backend never sends raw secrets."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

Hash = Annotated[str, Field(min_length=1, max_length=512)]
Email = Annotated[str, Field(min_length=3, max_length=320)]


class CredentialIn(BaseModel):
    password_hash: Hash


class CredentialOut(BaseModel):
    user_id: UUID
    password_hash: str


class SessionIn(BaseModel):
    user_id: UUID
    refresh_hash: Hash
    expires_at: datetime
    user_agent: Annotated[str, Field(max_length=500)] | None = None


class SessionCreated(BaseModel):
    id: UUID


class SessionOut(BaseModel):
    id: UUID
    user_id: UUID
    expires_at: datetime


class RotateIn(BaseModel):
    refresh_hash: Hash
    new_refresh_hash: Hash
    expires_at: datetime


class RotateOut(BaseModel):
    id: UUID
    user_id: UUID


class RevokeIn(BaseModel):
    refresh_hash: Hash


class LoginCodeIn(BaseModel):
    code_hash: Hash
    email: Email
    expires_at: datetime


class ConsumeIn(BaseModel):
    code_hash: Hash


class ConsumedOut(BaseModel):
    email: str


class OAuthIdentityIn(BaseModel):
    provider: Literal["google", "github"]
    subject: Annotated[str, Field(min_length=1, max_length=255)]
    user_id: UUID


class OAuthIdentityOut(BaseModel):
    user_id: UUID
