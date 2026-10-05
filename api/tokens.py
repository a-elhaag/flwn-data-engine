"""Workspace-scoped agent tokens (HS256 JWT).

The workspace lives in the signed token, never in tool arguments, so an agent cannot
reach another workspace by changing a parameter.
"""

import time
from dataclasses import dataclass

import jwt

from config import settings

SCOPE_READ = "memory:read"
SCOPE_WRITE = "memory:write"
SCOPE_DELETE = "memory:delete"
AGENT_SCOPES = frozenset({SCOPE_READ, SCOPE_WRITE, SCOPE_DELETE})


class TokenError(Exception):
    pass


@dataclass(frozen=True)
class Claims:
    workspace_id: str
    scopes: frozenset[str]
    subject: str
    expires_at: int


def enabled() -> bool:
    return bool(settings.MEMORY_TOKEN_SECRET)


def mint(
    workspace_id: str, scopes: set[str] | frozenset[str], subject: str, ttl_seconds: int
) -> tuple[str, int]:
    if not enabled():
        raise TokenError("tokens are disabled: MEMORY_TOKEN_SECRET is not set")
    unknown = set(scopes) - AGENT_SCOPES
    if unknown:
        raise TokenError(f"unknown scopes: {sorted(unknown)}")
    if not 1 <= ttl_seconds <= settings.MEMORY_TOKEN_MAX_TTL_SECONDS:
        raise TokenError(
            f"ttl_seconds must be 1-{settings.MEMORY_TOKEN_MAX_TTL_SECONDS}"
        )
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
    workspace_id = data["ws"]
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        raise TokenError("invalid token workspace")
    return Claims(
        workspace_id=workspace_id,
        scopes=frozenset(str(data.get("scope", "")).split()),
        subject=str(data["sub"]),
        expires_at=int(data["exp"]),
    )
