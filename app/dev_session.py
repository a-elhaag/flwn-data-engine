"""Development shortcut: sign in as anyone and get a personal and a shared workspace, with tokens.

This hands out admin tokens with no credentials, so it exists ONLY while the database is on this
machine (localhost, 127.0.0.1, ::1 or a local socket). Against any other database host the route
answers 404 and nothing is created. There is no switch to turn it on elsewhere.
"""

import hashlib

from sqlalchemy import func, select
from sqlalchemy.engine import make_url

from app.api import tokens
from app.db.models.identity import Member, User, Workspace
from app.db.session import database_url, service_session
from app.identity.members_teams import MemberService
from app.identity.workspaces import WorkspaceService

LOCAL_HOSTS = {None, "", "localhost", "127.0.0.1", "::1"}
SHARED_SLUG = "dev-shared"
TTL_SECONDS = 12 * 60 * 60


def enabled() -> bool:
    host = make_url(database_url()).host
    return host in LOCAL_HOSTS or str(host).startswith("/")  # a socket directory


def _workspace(slug: str, name: str) -> str:
    with service_session() as session:
        found = session.scalar(
            select(Workspace.id).where(
                func.lower(Workspace.slug) == slug, Workspace.deleted_at.is_(None)
            )
        )
    return str(found or WorkspaceService().create(name=name, slug=slug).id)


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
    key = hashlib.sha256(email.encode()).hexdigest()[:10]
    personal = _workspace(f"personal-{key}", f"{name} (personal)")
    shared = _workspace(SHARED_SLUG, "Dev shared")
    mine, user_id = _member(personal, email, name, "owner")
    theirs, _ = _member(shared, email, name, "admin")
    return {
        "user_id": user_id,
        "personal": _entry(personal, mine, user_id),
        "shared": _entry(shared, theirs, user_id),
    }
