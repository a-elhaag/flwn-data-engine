"""Workspace-scoped agent tokens (HS256 JWT).

The workspace lives in the signed token, never in tool arguments, so an agent cannot
reach another workspace by changing a parameter.
"""

import time
import uuid
from dataclasses import dataclass

import jwt

from app.config import settings

SCOPE_READ = "memory:read"
SCOPE_WRITE = "memory:write"
SCOPE_DELETE = "memory:delete"
SCOPE_FILES_READ = "files:read"
SCOPE_FILES_WRITE = "files:write"
SCOPE_FILES_DELETE = "files:delete"
AGENT_SCOPES = frozenset(
    {
        SCOPE_READ,
        SCOPE_WRITE,
        SCOPE_DELETE,
        SCOPE_FILES_READ,
        SCOPE_FILES_WRITE,
        SCOPE_FILES_DELETE,
    }
)


class TokenError(Exception):
    pass


@dataclass(frozen=True)
class Claims:
    workspace_id: str
    scopes: frozenset[str]
    subject: str
    expires_at: int


def _workspace(value) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        raise TokenError("workspace must be a UUID") from None


def enabled() -> bool:
    return bool(settings.MEMORY_TOKEN_SECRET)


def mint(
    workspace_id: str, scopes: set[str] | frozenset[str], subject: str, ttl_seconds: int
) -> tuple[str, int]:
    if not enabled():
        raise TokenError("tokens are disabled: MEMORY_TOKEN_SECRET is not set")
    workspace_id = _workspace(workspace_id)
    unknown = set(scopes) - AGENT_SCOPES
    if unknown:
        raise TokenError(f"unknown scopes: {sorted(unknown)}")
    if not 1 <= ttl_seconds <= settings.MEMORY_TOKEN_MAX_TTL_SECONDS:
        raise TokenError(f"ttl_seconds must be 1-{settings.MEMORY_TOKEN_MAX_TTL_SECONDS}")
    now = int(time.time())
    expires_at = now + ttl_seconds
    token = jwt.encode(
        {
            "iss": settings.MEMORY_TOKEN_ISSUER,
            "sub": subject,
            "ws": workspace_id,
            "scope": " ".join(sorted(scopes)),
            "iat": now,
            "exp": expires_at,
        },
        settings.MEMORY_TOKEN_SECRET,
        algorithm="HS256",
    )
    return token, expires_at


def verify(token: str) -> Claims:
    if not enabled():
        raise TokenError("tokens are disabled")
    try:
        data = jwt.decode(
            token,
            settings.MEMORY_TOKEN_SECRET,
            algorithms=["HS256"],
            issuer=settings.MEMORY_TOKEN_ISSUER,
            options={"require": ["exp", "iss", "sub", "ws"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError("invalid or expired token") from exc
    return Claims(
        workspace_id=_workspace(data["ws"]),
        scopes=frozenset(str(data.get("scope", "")).split()),
        subject=str(data["sub"]),
        expires_at=int(data["exp"]),
    )
