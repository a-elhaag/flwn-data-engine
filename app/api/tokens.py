"""Workspace-scoped tokens: verified here, signed by the backend.

The workspace lives in the signed token, never in tool arguments, so a caller cannot
reach another workspace by changing a parameter. The token can also name the member it acts as
(`mem`), which is how writes record who did them.

This service only verifies. Tokens are EdDSA or ES256, picked by `kid` from the backend's public
keys, and carry iss, aud, sub, ws, typ, ctx, scope, iat, exp and jti.
"""

import json
import uuid
from dataclasses import dataclass
from functools import lru_cache

import jwt
from jwt import PyJWKClient, PyJWKSet
from sqlalchemy.exc import SQLAlchemyError

from app.api import revocation
from app.core.config import settings

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
SCOPE_IDENTITY_READ = "identity:read"  # workspace, members, teams
SCOPE_IDENTITY_WRITE = "identity:write"
SCOPE_PLANNING_READ = "planning:read"  # projects, work items, tasks, sprints
SCOPE_PLANNING_WRITE = "planning:write"
SCOPE_COLLAB_READ = "collab:read"  # docs, channels, comments
SCOPE_COLLAB_WRITE = "collab:write"
SCOPE_ADMIN = "admin"  # workspace administrator: satisfies every scope check; never for atoms
CONTEXTS = frozenset(
    {"personal", "workspace"}
)  # the token's data context. For an atom token it is the atom's kind, fixed at mint
TYPES = frozenset({"human", "agent", "run", "service"})
SIGNED_ALGS = ("EdDSA", "ES256")
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
        SCOPE_IDENTITY_READ,
        SCOPE_IDENTITY_WRITE,
        SCOPE_PLANNING_READ,
        SCOPE_PLANNING_WRITE,
        SCOPE_COLLAB_READ,
        SCOPE_COLLAB_WRITE,
        SCOPE_ADMIN,
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
    ctx: str | None = None  # "personal" or "workspace"
    typ: str = "agent"  # human, agent, run or service
    jti: str | None = None
    obo: str | None = None  # the human an agent acts for (typ=agent)
    act: str | None = None  # the service that forwarded the call


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


def _atom_claims(member_id, atom_id, run_id, scopes, ttl_seconds, ctx):
    if (atom_id is None and run_id is None) != (ctx is None):
        raise TokenError("atom tokens require ctx, and only atom tokens carry it")
    if atom_id is not None or run_id is not None:
        if ctx not in CONTEXTS:
            raise TokenError("ctx must be personal or workspace")
        if not (atom_id and run_id and member_id):
            raise TokenError("atom tokens require atom_id, run_id and member_id")
        if SCOPE_ATOMS_ADMIN in scopes or SCOPE_ADMIN in scopes:
            raise TokenError("admin scopes are forbidden for atom tokens")
        if not 1 <= ttl_seconds <= 900:
            raise TokenError("atom ttl_seconds must be 1-900")


def verify(token: str) -> Claims:
    if not settings.AGENT_TOKENS_ENABLED:
        raise TokenError("tokens are disabled")
    try:
        alg = jwt.get_unverified_header(token).get("alg")
    except jwt.PyJWTError:
        raise TokenError("invalid or expired token") from None
    if alg not in SIGNED_ALGS:
        raise TokenError("invalid or expired token")
    return _verify_signed(token)


@lru_cache
def _jwks_client(url: str) -> PyJWKClient:
    return PyJWKClient(url, cache_keys=True, lifespan=300)


def _signing_key(token: str):
    if settings.TOKEN_JWKS:
        kid = jwt.get_unverified_header(token).get("kid")
        for key in PyJWKSet.from_dict(json.loads(settings.TOKEN_JWKS)).keys:
            if key.key_id == kid:
                return key.key
        raise TokenError("invalid or expired token")
    if settings.TOKEN_JWKS_URL:
        return _jwks_client(settings.TOKEN_JWKS_URL).get_signing_key_from_jwt(token).key
    raise TokenError("no token signing keys configured")


def _verify_signed(token: str) -> Claims:
    try:
        data = jwt.decode(
            token,
            _signing_key(token),
            algorithms=list(SIGNED_ALGS),
            issuer=settings.TOKEN_ISSUER,
            audience=settings.TOKEN_AUDIENCE,
            options={"require": ["exp", "iat", "iss", "aud", "sub", "ws", "typ", "ctx", "jti"]},
        )
    except (jwt.PyJWTError, ValueError, KeyError):
        raise TokenError("invalid or expired token") from None
    jti = str(data["jti"])
    try:
        revoked = revocation.is_revoked(jti)
    except (SQLAlchemyError, RuntimeError):
        raise TokenError("token status unavailable") from None  # fail closed
    if revoked:
        raise TokenError("token revoked")
    typ, ctx = data["typ"], data["ctx"]
    if typ not in TYPES or ctx not in CONTEXTS:
        raise TokenError("typ or ctx is not valid")
    member_id, obo = _member(data.get("mem")), _member(data.get("obo"))
    agent, run = _member(data.get("agent")), _member(data.get("run"))
    scopes = frozenset(str(data.get("scope", "")).split())
    ttl_seconds = int(data["exp"]) - int(data["iat"])
    if typ == "human":
        ok = member_id and not (obo or agent or run)
    elif typ == "agent":  # an assistant acting for a human: the audit trail needs both
        ok = member_id and obo and not run and agent in (None, member_id)
    elif typ == "run":
        ok = member_id and agent and run and not obo
        if ok:
            _atom_claims(member_id, agent, run, scopes, ttl_seconds, ctx)
    else:  # service: an engine with no user behind it
        ok = not (member_id or obo or agent or run)
    if not ok:
        raise TokenError(f"claims do not fit typ {typ}")
    return Claims(
        workspace_id=_workspace(data["ws"]),
        scopes=scopes,
        subject=str(data["sub"]),
        expires_at=int(data["exp"]),
        member_id=member_id,
        atom_id=agent if typ == "run" else None,
        run_id=run,
        ctx=ctx,
        typ=typ,
        jti=jti,
        obo=obo,
        act=str(data["act"]) if data.get("act") else None,
    )
