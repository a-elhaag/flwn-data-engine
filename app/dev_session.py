"""Development shortcut: sign in as anyone and get a personal and a shared workspace, with tokens.

This hands out admin tokens with no credentials, so it is limited three ways:
- the database must be on this machine (localhost, 127.0.0.1, ::1 or a local socket), else 404;
- the caller must connect from this machine (loopback), else 404;
- it only ever works with accounts it made itself (marked in the user's settings): an email that
  already belongs to a real user is refused, so it cannot be used to sign in as someone.
There is no switch to turn it on elsewhere.
"""

import hashlib
import os
import re

from sqlalchemy import func, select
from sqlalchemy.engine import make_url

from app.api import tokens
from app.db.models.identity import Member, User, Workspace
from app.db.session import database_url, service_session
from app.identity.members_teams import MemberService
from app.identity.workspaces import WorkspaceService

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
PG_ENV = ("PGHOST", "PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE")
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
MARKER = "dev_session"
SHARED_SLUG = "dev-shared"
TTL_SECONDS = 12 * 60 * 60


def _is_local(host: str) -> bool:
    return host in LOCAL_HOSTS or host.startswith("/")  # or a socket directory


def enabled() -> bool:
    """True only if every address the driver could connect to is on this machine.

    A URL with no host makes libpq fall back to PGHOST and friends, so those must be unset;
    `hostaddr` in the URL overrides `host`, and a comma-separated list is checked entry by entry.
    """
    url = make_url(database_url())
    query = url.query
    hosts = str(query.get("hostaddr") or query.get("host") or url.host or "")
    if not hosts:
        if any(os.environ.get(name) for name in PG_ENV):
            return False
        return True  # no host anywhere: libpq uses its default local socket
    return all(_is_local(host.strip()) for host in hosts.split(","))


class RealUser(PermissionError):  # a real account or workspace is in the way
    pass


def loopback(client_host: str | None) -> bool:
    return client_host in LOOPBACK


HOST_HEADER = re.compile(r"^(localhost|127\.0\.0\.1|\[::1\])(:[0-9]{1,5})?$", re.I)
PROXY_HEADERS = ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-real-ip", "origin")


def direct(headers) -> bool:
    """A request made straight to this machine's address, not through a browser page or a proxy.

    A web page can make a visitor's browser call 127.0.0.1 (DNS rebinding); such a request names
    another site in Host and carries Origin. A local reverse proxy adds Forwarded headers.
    """
    return bool(HOST_HEADER.match(headers.get("host", ""))) and not any(
        name in headers for name in PROXY_HEADERS
    )


def _claim_user(email: str) -> None:
    """Refuse an email that belongs to an account this tool did not create."""
    with service_session() as session:
        settings = session.scalar(select(User.settings).where(func.lower(User.email) == email))
    if settings is not None and not settings.get(MARKER):
        raise RealUser("that email belongs to a real account; dev sign-in only uses its own")


def _mark_user(user_id: str) -> None:
    with service_session() as session:
        user = session.get(User, user_id)
        if not user.settings.get(MARKER):
            user.settings = {**user.settings, MARKER: True}


def _workspace(slug: str, name: str) -> str:
    """The workspace with this slug, made by this tool; a real workspace by that name is refused."""
    with service_session() as session:
        found = session.execute(
            select(Workspace.id, Workspace.settings).where(
                func.lower(Workspace.slug) == slug, Workspace.deleted_at.is_(None)
            )
        ).first()
    if found is None:
        return str(WorkspaceService().create(name=name, slug=slug, settings={MARKER: True}).id)
    if not found.settings.get(MARKER):
        raise RealUser(f"a workspace called {slug} already exists and is not a dev workspace")
    return str(found.id)


def _member(workspace_id: str, email: str, name: str, role: str) -> tuple[str, str]:
    """(member id, user id) for this person in the workspace, created on first use."""
    with service_session() as session:
        found = session.execute(
            select(Member.id, Member.user_id)
            .join(User, User.id == Member.user_id)
            .where(
                Member.workspace_id == workspace_id,
                func.lower(User.email) == email,
                Member.deleted_at.is_(None),
            )
        ).first()
    if found is None:
        row = MemberService(workspace_id).create(
            type="HUMAN",
            role=role,
            status="active",
            name=name,
            email=email,
            agent_kind=None,
            avatar_url=None,
            settings={},
        )
        return str(row["id"]), str(row["user_id"])
    return str(found[0]), str(found[1])


def _entry(workspace_id: str, member_id: str, user_id: str) -> dict:
    token, expires_at = tokens.mint(
        workspace_id, set(tokens.AGENT_SCOPES), user_id, TTL_SECONDS, member_id
    )
    return {
        "workspace_id": workspace_id,
        "member_id": member_id,
        "token": token,
        "expires_at": expires_at,
    }


def sign_in(email: str, name: str) -> dict:
    """The person's own workspace (they own it) and the shared dev workspace (they administer it).

    Calling again with the same email returns the same workspaces and members.
    """
    email = email.strip().lower()
    _claim_user(email)
    key = hashlib.sha256(email.encode()).hexdigest()[:10]
    personal = _workspace(f"personal-{key}", f"{name} (personal)")
    shared = _workspace(SHARED_SLUG, "Dev shared")
    mine, user_id = _member(personal, email, name, "owner")
    theirs, _ = _member(shared, email, name, "admin")
    _mark_user(user_id)
    return {
        "user_id": user_id,
        "personal": _entry(personal, mine, user_id),
        "shared": _entry(shared, theirs, user_id),
    }
