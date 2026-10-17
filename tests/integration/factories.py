"""Helpers that create entities through the HTTP API (black-box test data)."""

import random
import string
import uuid
from dataclasses import dataclass

from fastapi.testclient import TestClient


def uniq(prefix: str = "x") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def random_key(n: int = 4) -> str:
    return "".join(random.choices(string.ascii_uppercase, k=n))


@dataclass(frozen=True)
class Org:
    id: str
    owner_id: str  # the creator's Member id
    user_id: str  # the creator's User id
    user_name: str
    user_email: str

    @property
    def h(self) -> dict:
        return {"X-Organization-Id": self.id}


class Api:
    """TestClient wrapper. `org` may be an Org (adds its header) or an id string."""

    def __init__(self, client: TestClient):
        self.client = client

    def request(self, method: str, path: str, org=None, headers=None, **kw):
        merged = dict(headers or {})
        if org is not None:
            merged["X-Organization-Id"] = org if isinstance(org, str) else org.id
        return self.client.request(method, path, headers=merged, **kw)

    def get(self, path, org=None, **kw):
        return self.request("GET", path, org, **kw)

    def post(self, path, org=None, json=None, **kw):
        return self.request("POST", path, org, json=json, **kw)

    def patch(self, path, org=None, json=None, **kw):
        return self.request("PATCH", path, org, json=json, **kw)

    def delete(self, path, org=None, **kw):
        return self.request("DELETE", path, org, **kw)


def ok(response, status: int):
    assert response.status_code == status, f"{response.status_code}: {response.text}"
    return response.json() if response.content else None


def make_user(api: Api, name: str | None = None, email: str | None = None) -> dict:
    body = {"name": name or uniq("User"), "email": email or f"{uniq('u')}@flwn.test"}
    return ok(api.post("/users", json=body), 201)


def make_org(api: Api, user: dict | None = None, name: str | None = None) -> Org:
    user = user or make_user(api)
    created = ok(
        api.post(
            "/organizations",
            json={"name": name or uniq("Org"), "createdById": user["id"]},
        ),
        201,
    )
    return Org(
        id=created["organization"]["id"],
        owner_id=created["member"]["id"],
        user_id=user["id"],
        user_name=user["name"],
        user_email=user["email"],
    )


def make_workspace(api: Api, org: Org, name: str | None = None, **extra) -> dict:
    body = {"name": name or uniq("WS"), "createdById": org.owner_id, **extra}
    return ok(api.post(f"/organizations/{org.id}/workspaces", json=body), 201)


def make_team(api: Api, org: Org, workspace: dict, name: str | None = None) -> dict:
    body = {"name": name or uniq("Team"), "createdById": org.owner_id}
    return ok(api.post(f"/workspaces/{workspace['id']}/teams", org, json=body), 201)


def make_ai_member(api: Api, org: Org, name: str | None = None, **extra) -> dict:
    body = {"type": "AI", "name": name or uniq("Bot"), "createdById": org.owner_id, **extra}
    return ok(api.post(f"/organizations/{org.id}/members", json=body), 201)


def make_human_member(api: Api, org: Org, user: dict | None = None, **extra) -> dict:
    user = user or make_user(api)
    body = {"type": "HUMAN", "userId": user["id"], "createdById": org.owner_id, **extra}
    return ok(api.post(f"/organizations/{org.id}/members", json=body), 201)


def make_project(
    api: Api, org: Org, workspace: dict, key: str | None = None, name: str | None = None, **extra
) -> dict:
    body = {
        "key": key or random_key(),
        "name": name or uniq("Project"),
        "createdById": org.owner_id,
        **extra,
    }
    return ok(api.post(f"/workspaces/{workspace['id']}/projects", org, json=body), 201)


def add_team_to_project(api: Api, org: Org, project: dict, team: dict) -> dict:
    return ok(api.post(f"/projects/{project['id']}/teams", org, json={"teamId": team["id"]}), 201)


def add_team_member(api: Api, org: Org, team: dict, member_id: str, role: str | None = None):
    body = {"memberId": member_id, **({"role": role} if role else {})}
    return ok(api.post(f"/teams/{team['id']}/members", org, json=body), 201)


def make_sprint(
    api: Api, org: Org, project: dict, team: dict, name: str | None = None, **extra
) -> dict:
    body = {"name": name or uniq("Sprint"), "createdById": org.owner_id, **extra}
    path = f"/projects/{project['id']}/teams/{team['id']}/sprints"
    return ok(api.post(path, org, json=body), 201)


def make_work_item(api: Api, org: Org, project: dict, title: str | None = None, **extra) -> dict:
    body = {"title": title or uniq("WI"), "type": "FEATURE", "createdById": org.owner_id, **extra}
    return ok(api.post(f"/projects/{project['id']}/work-items", org, json=body), 201)


def make_task(api: Api, org: Org, project: dict, title: str | None = None, **extra) -> dict:
    body = {"title": title or uniq("Task"), "createdById": org.owner_id, **extra}
    return ok(api.post(f"/projects/{project['id']}/tasks", org, json=body), 201)
