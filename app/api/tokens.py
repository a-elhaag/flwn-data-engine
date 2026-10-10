"""Workspace-scoped agent tokens (HS256 JWT).

The workspace lives in the signed token, never in tool arguments, so an agent cannot
reach another workspace by changing a parameter. The token can also name the member it acts as
(`mem`), which is how writes record who did them.
"""

import time
import uuid
from dataclasses import dataclass

import jwt

from app.config import settings

SCOPE_READ = "memory:read"
SCOPE_WRITE = "memory:write"
SCOPE_DELETE = "memory:delete"
SCOPE_MAINTAIN = "memory:maintain"  # run cleanup (also at sprint end); never granted by default
SCOPE_FILES_READ = "files:read"
SCOPE_FILES_WRITE = "files:write"
SCOPE_FILES_DELETE = "files:delete"
SCOPE_MEETINGS_READ = "meetings:read"
SCOPE_MEETINGS_WRITE = "meetings:write"
SCOPE_CONFLICTS_READ = "conflicts:read"
SCOPE_CONFLICTS_WRITE = "conflicts:write"  # flag only; resolving is for human members
SCOPE_ATOMS_READ = "atoms:read"
SCOPE_ATOMS_RUN = "atoms:run"
SCOPE_ATOMS_PROPOSE = "atoms:propose"
SCOPE_SKILLS_READ = "skills:read"
SCOPE_SKILLS_WRITE = "skills:write"
SCOPE_ATOMS_ADMIN = "atoms:admin"
SCOPE_AGGREGATE_READ = "aggregate:read"
ATOM_SCOPES = frozenset(
    {
        SCOPE_ATOMS_READ,
        SCOPE_ATOMS_RUN,
        SCOPE_ATOMS_PROPOSE,
        SCOPE_SKILLS_READ,
        SCOPE_SKILLS_WRITE,
        SCOPE_AGGREGATE_READ,
    }
)
AGENT_SCOPES = frozenset(
    {
        SCOPE_READ,
        SCOPE_WRITE,
        SCOPE_DELETE,
        SCOPE_MAINTAIN,
        SCOPE_FILES_READ,
        SCOPE_FILES_WRITE,
        SCOPE_FILES_DELETE,
        SCOPE_MEETINGS_READ,
        SCOPE_MEETINGS_WRITE,
        SCOPE_CONFLICTS_READ,
        SCOPE_CONFLICTS_WRITE,
        SCOPE_ATOMS_READ,
        SCOPE_ATOMS_RUN,
        SCOPE_ATOMS_PROPOSE,
        SCOPE_SKILLS_READ,
        SCOPE_SKILLS_WRITE,
        SCOPE_ATOMS_ADMIN,
        SCOPE_AGGREGATE_READ,
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
    member_id: str | None = None  # the member (human or AI agent) this token acts as
    atom_id: str | None = None
    run_id: str | None = None


def _workspace(value) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        raise TokenError("workspace must be a UUID") from None


def _member(value) -> str | None:
    if value is None:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        raise TokenError("member must be a UUID") from None


def enabled() -> bool:
    return bool(settings.MEMORY_TOKEN_SECRET)


def mint(
    workspace_id: str,
    scopes: set[str] | frozenset[str],
    subject: str,
    ttl_seconds: int,
    member_id: str | None = None,
    atom_id: str | None = None,
    run_id: str | None = None,
) -> tuple[str, int]:
    if not enabled():
        raise TokenError("tokens are disabled: MEMORY_TOKEN_SECRET is not set")
    workspace_id = _workspace(workspace_id)
    member_id = _member(member_id)
    atom_id, run_id = _member(atom_id), _member(run_id)
    _atom_claims(member_id, atom_id, run_id, scopes, ttl_seconds)
    unknown = set(scopes) - AGENT_SCOPES
    if unknown:
        raise TokenError(f"unknown scopes: {sorted(unknown)}")
    if not 1 <= ttl_seconds <= settings.MEMORY_TOKEN_MAX_TTL_SECONDS:
        raise TokenError(f"ttl_seconds must be 1-{settings.MEMORY_TOKEN_MAX_TTL_SECONDS}")
    now = int(time.time())
    expires_at = now + ttl_seconds
    claims = {
        "iss": settings.MEMORY_TOKEN_ISSUER,
        "sub": subject,
        "ws": workspace_id,
        "scope": " ".join(sorted(scopes)),
        "iat": now,
        "exp": expires_at,
    }
    if member_id:
        claims["mem"] = member_id
    if atom_id:
        claims.update(atom_id=atom_id, run_id=run_id)
    token = jwt.encode(claims, settings.MEMORY_TOKEN_SECRET, algorithm="HS256")
    return token, expires_at


def _atom_claims(member_id, atom_id, run_id, scopes, ttl_seconds):
    if atom_id is not None or run_id is not None:
        if not (atom_id and run_id and member_id):
            raise TokenError("atom tokens require atom_id, run_id and member_id")
        if SCOPE_ATOMS_ADMIN in scopes:
            raise TokenError("atoms:admin is forbidden for atom tokens")
        if not 1 <= ttl_seconds <= 900:
            raise TokenError("atom ttl_seconds must be 1-900")


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
    atom_id, run_id = _member(data.get("atom_id")), _member(data.get("run_id"))
    member_id = _member(data.get("mem"))
    scopes = frozenset(str(data.get("scope", "")).split())
    try:
        ttl_seconds = int(data["exp"]) - int(data["iat"]) if atom_id or run_id else 0
    except (KeyError, TypeError, ValueError, OverflowError):
        raise TokenError("atom tokens require valid iat and exp") from None
    _atom_claims(member_id, atom_id, run_id, scopes, ttl_seconds)
    return Claims(
        workspace_id=_workspace(data["ws"]),
        scopes=frozenset(str(data.get("scope", "")).split()),
        subject=str(data["sub"]),
        expires_at=int(data["exp"]),
        member_id=member_id,
        atom_id=atom_id,
        run_id=run_id,
    )
