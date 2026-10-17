import uuid

import pytest

import factories as f
from factories import assert_error


def test_create_ai_member_with_name_and_optional_email(api, org):
    response = api.post(
        f"/organizations/{org.id}/members",
        json={"type": "AI", "name": "Ghost", "email": "Ghost@Flwn.io", "createdById": org.owner_id},
    )
    assert response.status_code == 201
    body = response.json()
    assert (body["type"], body["name"], body["email"]) == ("AI", "Ghost", "ghost@flwn.io")
    assert (body["role"], body["status"], body["userId"]) == ("MEMBER", "ACTIVE", None)


def test_ai_member_without_name_returns_422(api, org):
    response = api.post(f"/organizations/{org.id}/members", json={"type": "AI"})
    assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"


def test_human_member_takes_name_and_email_from_the_user(api, org, user):
    member = f.make_human_member(api, org, user)
    assert (member["type"], member["name"], member["email"]) == ("HUMAN", user["name"], user["email"])


def test_human_member_requires_user_id(api, org):
    response = api.post(f"/organizations/{org.id}/members", json={"type": "HUMAN"})
    assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"


def test_human_member_with_a_name_returns_422(api, org, user):
    body = {"type": "HUMAN", "userId": user["id"], "name": "Nope"}
    response = api.post(f"/organizations/{org.id}/members", json=body)
    assert response.status_code == 422 and response.json()["code"] == "VALIDATION_ERROR"


def test_ai_name_duplicate_in_another_case_returns_409(api, org):
    f.make_ai_member(api, org, name="Ghost")
    response = api.post(f"/organizations/{org.id}/members", json={"type": "AI", "name": "ghost"})
    assert_error(response, 409, "MEMBER_ALREADY_EXISTS", "name")


def test_ai_email_duplicate_returns_409(api, org):
    f.make_ai_member(api, org, email="bot@flwn.io")
    response = api.post(
        f"/organizations/{org.id}/members",
        json={"type": "AI", "name": "Other", "email": "BOT@flwn.io"},
    )
    assert_error(response, 409, "MEMBER_ALREADY_EXISTS", "email")


def test_two_ai_members_without_email_are_allowed(api, org):
    f.make_ai_member(api, org)
    f.make_ai_member(api, org)


def test_same_ai_name_in_two_organizations_is_allowed(api, org, other_org):
    f.make_ai_member(api, org, name="Ghost")
    f.make_ai_member(api, other_org, name="Ghost")


def test_same_user_twice_in_one_organization_returns_409(api, org):
    body = {"type": "HUMAN", "userId": org.user_id}
    response = api.post(f"/organizations/{org.id}/members", json=body)
    assert_error(response, 409, "MEMBER_ALREADY_EXISTS", "userId")


def test_same_user_in_two_organizations_is_allowed(api, user):
    first, second = f.make_org(api, user), f.make_org(api, user)
    assert first.id != second.id


def test_workspace_of_another_organization_returns_422(api, org, other_org):
    foreign = f.make_workspace(api, other_org)
    body = {"type": "AI", "name": "Bot", "workspaceId": foreign["id"]}
    response = api.post(f"/organizations/{org.id}/members", json=body)
    assert_error(response, 422, "INVALID_REFERENCE", "workspaceId")


def test_unknown_user_id_returns_422(api, org):
    body = {"type": "HUMAN", "userId": str(uuid.uuid4())}
    response = api.post(f"/organizations/{org.id}/members", json=body)
    assert_error(response, 422, "INVALID_REFERENCE", "userId")


def test_member_with_no_workspace_is_allowed(api, org):
    assert f.make_ai_member(api, org)["workspaceId"] is None


def test_get_member(api, org, ai_member):
    assert api.get(f"/members/{ai_member['id']}", org).json()["id"] == ai_member["id"]


def test_patch_role_status_and_workspace(api, org, workspace, ai_member):
    response = api.patch(
        f"/members/{ai_member['id']}",
        org,
        json={"role": "OWNER", "status": "INACTIVE", "workspaceId": workspace["id"]},
    )
    body = response.json()
    assert (body["role"], body["status"], body["workspaceId"]) == ("OWNER", "INACTIVE", workspace["id"])


def test_patch_workspace_id_null_clears_the_home_department(api, org, workspace):
    member = f.make_ai_member(api, org, workspaceId=workspace["id"])
    response = api.patch(f"/members/{member['id']}", org, json={"workspaceId": None})
    assert response.json()["workspaceId"] is None


def test_patch_name_on_a_human_returns_422_on_type(api, org):
    response = api.patch(f"/members/{org.owner_id}", org, json={"name": "Nope"})
    assert_error(response, 422, "VALIDATION_ERROR", "type")


def test_patch_cannot_change_type_or_user(api, org, ai_member):
    for body in ({"type": "HUMAN"}, {"userId": org.user_id}):
        assert_error(api.patch(f"/members/{ai_member['id']}", org, json=body), 422, "VALIDATION_ERROR")


def test_list_filters(api, org, workspace):
    bot = f.make_ai_member(api, org, workspaceId=workspace["id"])
    api.patch(f"/members/{bot['id']}", org, json={"status": "INACTIVE"})
    path = f"/organizations/{org.id}/members"

    def ids(**params):
        return {m["id"] for m in api.get(path, params=params).json()["items"]}

    assert ids(type="AI") == {bot["id"]}
    assert ids(type="HUMAN") == {org.owner_id}
    assert ids(status="INACTIVE") == {bot["id"]}
    assert ids(status="ACTIVE") == {org.owner_id}
    assert ids(role="OWNER") == {org.owner_id}
    assert ids(role="MEMBER") == {bot["id"]}
    assert ids(workspaceId=workspace["id"]) == {bot["id"]}
    assert ids(type="AI", status="ACTIVE") == set()


def test_list_filter_with_invalid_value_returns_422(api, org):
    response = api.get(f"/organizations/{org.id}/members", params={"type": "ROBOT"})
    assert_error(response, 422, "VALIDATION_ERROR", "type")


def test_member_teams(api, org, workspace, team, ai_member):
    other_team = f.make_team(api, org, workspace)
    f.add_team_member(api, org, team, ai_member["id"], "LEAD")
    f.add_team_member(api, org, other_team, ai_member["id"])
    items = api.get(f"/members/{ai_member['id']}/teams", org).json()["items"]
    assert {(t["teamId"], t["role"]) for t in items} == {(team["id"], "LEAD"), (other_team["id"], "MEMBER")}


def test_member_projects_are_distinct_and_go_through_teams(api, org, workspace, project, team, ai_member):
    second_team = f.make_team(api, org, workspace)
    for t in (team, second_team):
        f.add_team_to_project(api, org, project, t)
        f.add_team_member(api, org, t, ai_member["id"])
    other_project = f.make_project(api, org, workspace)  # none of the member's teams
    items = api.get(f"/members/{ai_member['id']}/projects", org).json()["items"]
    assert [p["id"] for p in items] == [project["id"]]
    assert other_project["id"] not in [p["id"] for p in items]


def test_member_projects_include_cross_department_projects(api, org, workspace, project, ai_member):
    hr = f.make_workspace(api, org, name="HR")
    recruiting = f.make_team(api, org, hr)
    f.add_team_to_project(api, org, project, recruiting)
    f.add_team_member(api, org, recruiting, ai_member["id"])
    items = api.get(f"/members/{ai_member['id']}/projects", org).json()["items"]
    assert [p["id"] for p in items] == [project["id"]]


def test_member_tasks_span_projects_and_filter_by_status(api, org, workspace, project, ai_member):
    second_project = f.make_project(api, org, workspace)
    first = f.make_task(api, org, project, assigneeId=ai_member["id"])
    second = f.make_task(api, org, second_project, assigneeId=ai_member["id"], status="BLOCKED")
    f.make_task(api, org, project)  # unassigned
    path = f"/members/{ai_member['id']}/tasks"
    assert {t["id"] for t in api.get(path, org).json()["items"]} == {first["id"], second["id"]}
    blocked = api.get(path, org, params={"status": "BLOCKED"}).json()["items"]
    assert [t["id"] for t in blocked] == [second["id"]]
    assert blocked[0]["key"] == f"{second_project['key']}-1"


def test_member_sub_resources_of_unknown_member_return_404(api, org):
    for suffix in ("teams", "projects", "tasks"):
        assert_error(api.get(f"/members/{uuid.uuid4()}/{suffix}", org), 404, "MEMBER_NOT_FOUND")


@pytest.mark.xfail(
    strict=True,
    reason="MCP app mounted at '/' answers unmatched methods with 401; fixed in the agent step",
)
def test_delete_member_is_not_allowed_returns_405(api, org, ai_member):
    assert api.delete(f"/members/{ai_member['id']}", org).status_code == 405
