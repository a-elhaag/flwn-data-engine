"""Row builders and keys shared by the database and token tests."""

import json
import uuid
from decimal import Decimal

import env  # noqa: F401  (sets the environment the app needs to import)
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jwt.algorithms import OKPAlgorithm
from sqlalchemy import insert

import app.db.models  # noqa: F401
from app.db.base import Base

T = lambda name: Base.metadata.tables[name]  # noqa: E731


def add(conn, table: str, **values) -> uuid.UUID:
    return conn.execute(insert(T(table)).values(**values).returning(T(table).c.id)).scalar_one()


def make_workspace(conn, slug: str) -> dict:
    """A workspace with a human, an agent, a team, and a project with one backlog item."""
    ws = {"workspace_id": add(conn, "workspaces", name=slug, slug=slug)}
    user = add(conn, "users", email=f"{slug}@example.com", name=slug)
    ws["human"] = add(conn, "members", workspace_id=ws["workspace_id"], user_id=user, type="HUMAN")
    ws["agent"] = add(
        conn,
        "members",
        workspace_id=ws["workspace_id"],
        type="AI",
        name="Ghost",
        agent_kind="ghost_engineer",
    )
    w = ws["workspace_id"]
    ws["team"] = add(conn, "teams", workspace_id=w, key="ENG", name="Engineering")
    ws["project"] = add(
        conn,
        "projects",
        workspace_id=w,
        team_id=ws["team"],
        key="FLWN",
        name="Flwn",
        created_by=ws["human"],
    )
    ws["backlog"] = add(conn, "backlogs", workspace_id=w, project_id=ws["project"], name="Main")
    ws["item"] = add(
        conn,
        "work_items",
        workspace_id=w,
        project_id=ws["project"],
        backlog_id=ws["backlog"],
        title="Login",
        type="STORY",
    )
    return ws


def make_atom(conn, workspace, **values):
    member_id = add(
        conn,
        "members",
        workspace_id=workspace["workspace_id"],
        type="AI",
        agent_kind="atom",
        name="Reporter",
    )
    atom = {
        "workspace_id": workspace["workspace_id"],
        "member_id": member_id,
        "kind": "workspace",
        "name": "Reporter",
        "model_tier": "small",
        "max_runs_per_day": 10,
        "max_cost_per_day": Decimal("2.50"),
        "max_actions_per_day": 50,
    }
    atom.update(values)
    atom["id"] = add(conn, "atoms", **atom)
    return atom


def make_version(conn, atom, **values):
    data = {
        "workspace_id": atom["workspace_id"],
        "atom_id": atom["id"],
        "version": 1,
        "instructions": "Summarize project updates.",
        "source": "human",
        "created_by": atom["member_id"],
    }
    data.update(values)
    return add(conn, "atom_versions", **data)


def make_schedule(conn, atom, **values):
    data = {"workspace_id": atom["workspace_id"], "atom_id": atom["id"], "interval_minutes": 5}
    data.update(values)
    return add(conn, "atom_schedules", **data)


def make_connection(conn, atom, connected_by, **values):
    data = {
        "workspace_id": atom["workspace_id"],
        "atom_id": atom["id"],
        "toolkit": "outlook",
        "composio_user_id": f"workspace:{atom['workspace_id']}",
        "toolkit_version": "20261001_00",
        "permission_ceiling": "read",
        "connected_by": connected_by,
    }
    data.update(values)
    return add(conn, "atom_connections", **data)


def make_skill(conn, workspace=None, **values):
    data = {
        "name": "summarize",
        "version": 1,
        "description": "Summarize updates",
        "when_to_use": "For a daily report",
        "instructions": "Do not include raw data.",
    }
    if workspace is not None:
        data.update(workspace_id=workspace["workspace_id"], scope="workspace")
    data.update(values)
    return add(conn, "skills" if workspace is not None else "catalog_skills", **data)


KEY = Ed25519PrivateKey.generate()


JWKS = json.dumps(
    {"keys": [{**OKPAlgorithm.to_jwk(KEY.public_key(), as_dict=True), "kid": "k1", "use": "sig"}]}
)
