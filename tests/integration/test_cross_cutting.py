import uuid

import pytest

from config import settings
import factories as f
from factories import assert_error

MISSING = "00000000-0000-0000-0000-000000000000"


# --- authentication -----------------------------------------------------------------


def test_missing_service_key_returns_401(anon):
    assert anon.get(f"/users/{MISSING}").status_code == 401


def test_wrong_service_key_returns_401(app):
    from fastapi.testclient import TestClient

    client = f.Api(TestClient(app, headers={"X-Data-API-Key": "wrong"}))
    assert client.get(f"/users/{MISSING}").status_code == 401


def test_agent_bearer_token_on_a_domain_route_returns_401(anon, monkeypatch):
    from api import tokens

    monkeypatch.setattr(settings, "MEMORY_TOKEN_SECRET", "s" * 40)
    token, _ = tokens.mint("ws-1", tokens.AGENT_SCOPES, "agent", 60)
    response = anon.get(f"/users/{MISSING}", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


# --- organization header -----------------------------------------------------------


def test_missing_organization_header_returns_400(api, org, workspace):
    response = api.get(f"/workspaces/{workspace['id']}")
    assert_error(response, 400, "ORGANIZATION_ID_REQUIRED", None)


def test_organization_header_that_is_not_a_uuid_returns_422(api, workspace):
    response = api.get(f"/workspaces/{workspace['id']}", org="not-a-uuid")
    assert_error(response, 422, "VALIDATION_ERROR", "X-Organization-Id")


def test_org_path_routes_work_without_the_header(api, org):
    assert api.get(f"/organizations/{org.id}/members").status_code == 200


def test_org_path_routes_ignore_a_wrong_header(api, org, other_org):
    response = api.get(f"/organizations/{org.id}/members", org=other_org)
    assert response.status_code == 200
    assert all(m["organizationId"] == org.id for m in response.json()["items"])


def test_unknown_org_id_in_path_returns_404(api):
    assert_error(api.get(f"/organizations/{uuid.uuid4()}/members"), 404, "ORGANIZATION_NOT_FOUND")


def test_global_routes_need_no_org_header(api, user):
    assert api.get(f"/users/{user['id']}").status_code == 200


# --- tenant isolation: a row of org A looks missing to org B ----------------------------------------


def _member(api, org, **_):
    return f.make_ai_member(api, org)["id"]


def _workspace(api, org, **_):
    return f.make_workspace(api, org)["id"]


def _team(api, org, **_):
    return f.make_team(api, org, f.make_workspace(api, org))["id"]


def _project(api, org, **_):
    return f.make_project(api, org, f.make_workspace(api, org))["id"]


def _work_item(api, org, **_):
    return f.make_work_item(api, org, f.make_project(api, org, f.make_workspace(api, org)))["id"]


def _sprint(api, org, **_):
    workspace = f.make_workspace(api, org)
    team = f.make_team(api, org, workspace)
    project = f.make_project(api, org, workspace)
    f.add_team_to_project(api, org, project, team)
    return f.make_sprint(api, org, project, team)["id"]


def _task(api, org, **_):
    return f.make_task(api, org, f.make_project(api, org, f.make_workspace(api, org)))["id"]


# entity, path template, builder, PATCH body, has DELETE
ENTITIES = [
    ("MEMBER", "/members/{}", _member, {"status": "INACTIVE"}, False),
    ("WORKSPACE", "/workspaces/{}", _workspace, {"name": "Renamed"}, True),
    ("TEAM", "/teams/{}", _team, {"name": "Renamed"}, True),
    ("PROJECT", "/projects/{}", _project, {"name": "Renamed"}, True),
    ("WORK_ITEM", "/work-items/{}", _work_item, {"title": "Renamed"}, True),
    ("SPRINT", "/sprints/{}", _sprint, {"name": "Renamed"}, True),
    ("TASK", "/tasks/{}", _task, {"title": "Renamed"}, True),
]


@pytest.mark.parametrize("entity,template,build,patch,has_delete", ENTITIES, ids=[e[0] for e in ENTITIES])
def test_other_organizations_header_sees_the_row_as_404(
    api, org, other_org, entity, template, build, patch, has_delete
):
    path = template.format(build(api, org))
    not_found = f"{entity}_NOT_FOUND"
    assert api.get(path, org=org).status_code == 200  # visible to its own organization
    assert_error(api.get(path, org=other_org), 404, not_found)
    assert_error(api.patch(path, org=other_org, json=patch), 404, not_found)
    if has_delete:
        assert_error(api.delete(path, org=other_org), 404, not_found)
    assert api.get(path, org=org).status_code == 200  # still untouched


# --- error body and validation format -------------------------------------------------


def test_error_body_has_exactly_code_message_field(api, org):
    response = api.get(f"/tasks/{uuid.uuid4()}", org=org)
    assert_error(response, 404, "TASK_NOT_FOUND", None)


def test_validation_error_field_is_camel_case(api, org, workspace):
    body = {"name": "T", "createdById": org.owner_id, "someUnknownField": 1}
    response = api.post(f"/workspaces/{workspace['id']}/teams", org, json=body)
    assert_error(response, 422, "VALIDATION_ERROR", "someUnknownField")


def test_invalid_query_filter_returns_422_with_camel_case_field(api, org, project):
    response = api.get(f"/projects/{project['id']}/tasks", org, params={"assigneeId": "nope"})
    assert_error(response, 422, "VALIDATION_ERROR", "assigneeId")


def test_integrity_error_field_is_camel_case(api, org, project):
    body = {"title": "T", "createdById": org.owner_id, "workItemId": str(uuid.uuid4())}
    assert_error(
        api.post(f"/projects/{project['id']}/tasks", org, json=body),
        422, "INVALID_REFERENCE", "workItemId",
    )  # fmt: skip


def test_memory_routes_keep_fastapis_default_422_format(api):
    response = api.post(f"/workspaces/{uuid.uuid4()}/memories", json={})
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    assert "code" not in response.json()


# --- pagination ---------------------------------------------------------------------------------


def test_default_page_size_is_50(api, org):
    page = api.get(f"/organizations/{org.id}/members").json()
    assert (page["limit"], page["offset"], page["hasMore"]) == (50, 0, False)


@pytest.mark.parametrize("params", [{"limit": 201}, {"limit": 0}, {"offset": -1}])
def test_out_of_range_pagination_returns_422(api, org, params):
    response = api.get(f"/organizations/{org.id}/members", params=params)
    assert_error(response, 422, "VALIDATION_ERROR", next(iter(params)))


def test_limit_200_is_accepted(api, org):
    assert api.get(f"/organizations/{org.id}/members", params={"limit": 200}).status_code == 200


def test_has_more_is_true_until_the_last_page(api, org):
    for _ in range(4):
        f.make_ai_member(api, org)  # 5 members with the owner
    path = f"/organizations/{org.id}/members"
    first = api.get(path, params={"limit": 2, "offset": 0}).json()
    last = api.get(path, params={"limit": 2, "offset": 4}).json()
    exact = api.get(path, params={"limit": 5}).json()
    assert (len(first["items"]), first["hasMore"]) == (2, True)
    assert (len(last["items"]), last["hasMore"]) == (1, False)
    assert (len(exact["items"]), exact["hasMore"]) == (5, False)


def test_pages_are_stable_and_do_not_overlap(api, org):
    for _ in range(5):
        f.make_ai_member(api, org)
    path = f"/organizations/{org.id}/members"
    everything = [m["id"] for m in api.get(path).json()["items"]]
    paged = []
    for offset in range(0, 6, 2):
        paged += [m["id"] for m in api.get(path, params={"limit": 2, "offset": offset}).json()["items"]]
    assert paged == everything and len(set(paged)) == 6


def test_tasks_are_listed_by_number(api, org, project):
    for _ in range(4):
        f.make_task(api, org, project)
    numbers = [t["number"] for t in api.get(f"/projects/{project['id']}/tasks", org).json()["items"]]
    assert numbers == [1, 2, 3, 4]


# --- response shape -----------------------------------------------------------------------------


def _all_keys(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _all_keys(v)
    elif isinstance(value, list):
        for v in value:
            yield from _all_keys(v)


def test_response_keys_are_camel_case(api, org, task):
    page = api.get(f"/projects/{task['projectId']}/tasks", org).json()
    created = api.post("/organizations", json={"name": "N", "createdById": org.user_id}).json()
    keys = set(_all_keys(page)) | set(_all_keys(created)) | set(_all_keys(task))
    assert "acceptanceCriteria" in keys and "hasMore" in keys
    assert not [k for k in keys if "_" in k]


@pytest.mark.parametrize("path", ["/docs", "/openapi.json"])
def test_documentation_endpoints_respond(anon, path):
    assert anon.get(path).status_code == 200
