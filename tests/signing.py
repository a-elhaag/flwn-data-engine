"""Backend-style signed tokens for tests. The app only verifies; this is the backend's side."""

import time
import uuid

import jwt
from factories import KEY

from app.core.config import settings


def mint(
    workspace_id,
    scopes,
    subject,
    ttl_seconds,
    member_id=None,
    atom_id=None,
    run_id=None,
    ctx=None,
    key=KEY,
):
    """A signed token. A member makes it a human's, an atom run makes it a run's, else a service's."""
    now = int(time.time())
    claims = {
        "iss": settings.TOKEN_ISSUER,
        "aud": [settings.TOKEN_AUDIENCE],
        "sub": subject,
        "ws": str(workspace_id),
        "typ": "run" if atom_id else ("human" if member_id else "service"),
        "ctx": ctx or "workspace",
        "scope": " ".join(sorted(scopes)),
        "iat": now,
        "exp": now + ttl_seconds,
        "jti": uuid.uuid4().hex,
    }
    if member_id:
        claims["mem"] = str(member_id)
    if atom_id:
        claims.update(agent=str(atom_id), run=str(run_id))
    return jwt.encode(claims, key, algorithm="EdDSA", headers={"kid": "k1"}), claims["exp"]
