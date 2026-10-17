import uuid

import factories as f
from factories import assert_error


def test_create_team(api, org, workspace):
    response = api.post(
        f"/workspaces/{workspace['id']}/teams",
        org,
        json={"name": "Backend", "description": "APIs", "createdById": org.owner_id},
    )
    assert response.status_code == 201
    body = response.json()
    assert (body["name"], body["workspaceId"], body["organizationId"]) == ("Backend", workspace["id"], org.id)


def test_team_in_unknown_workspace_returns_404(api, org):
    response = api.post(f"/workspaces/{uuid.uuid4()}/teams", org, json={"name": "T", "createdById": org.owner_id})
    assert_error(response, 404, "WORKSPACE_NOT_FOUND")


def test_duplicate_name_in_one_workspace_returns_409(api, org, workspace):
    f.make_team(api, org, workspace, name="Backend")
    response = api.post(
        f"/workspaces/{workspace['id']}/teams", org, json={"name": "BACKEND", "createdById": org.owner_id}
    )
    assert_error(response, 409, "TEAM_ALREADY_EXISTS", "name")


def test_same_name_in_another_workspace_is_allowed(api, org, workspace):
    f.make_team(api, org, workspace, name="Backend")
    f.make_team(api, org, f.make_workspace(api, org), name="Backend")


def test_name_of_an_archived_team_can_be_reused(api, org, workspace):
    old = f.make_team(api, org, workspace, name="Backend")
    api.delete(f"/teams/{old['id']}", org)
    f.make_team(api, org, workspace, name="backend")


def test_get_patch_archive_and_restore(api, org, team):
    path = f"/teams/{team['id']}"
    assert api.patch(path, org, json={"name": "Renamed"}).json()["name"] == "Renamed"
    assert api.delete(path, org).status_code == 204
    assert api.get(path, org).json()["archivedAt"] is not None
    assert api.patch(path, org, json={"archived": False}).json()["archivedAt"] is None


def test_list_teams_with_archived_filter(api, org, workspace, team):
    archived = f.make_team(api, org, workspace)
    api.delete(f"/teams/{archived['id']}", org)
    path = f"/workspaces/{workspace['id']}/teams"

    def ids(**params):
        return {t["id"] for t in api.get(path, org, params=params).json()["items"]}

    assert ids() == {team["id"], archived["id"]}
    assert ids(archived="true") == {archived["id"]}
    assert ids(archived="false") == {team["id"]}


# --- team members ---------------------------------------------------------------------


def test_added_member_gets_role_member_by_default(api, org, team, ai_member):
    added = f.add_team_member(api, org, team, ai_member["id"])
    assert (added["role"], added["teamId"], added["memberId"]) == ("MEMBER", team["id"], ai_member["id"])


def test_member_can_be_added_as_lead(api, org, team, ai_member):
    assert f.add_team_member(api, org, team, ai_member["id"], "LEAD")["role"] == "LEAD"


def test_adding_the_same_member_twice_returns_409(api, org, team, ai_member):
    f.add_team_member(api, org, team, ai_member["id"])
    response = api.post(f"/teams/{team['id']}/members", org, json={"memberId": ai_member["id"]})
    assert_error(response, 409, "TEAM_MEMBER_ALREADY_EXISTS", "memberId")


def test_member_from_another_department_of_the_same_org_is_allowed(api, org, team):
    other_dept = f.make_workspace(api, org)
    member = f.make_ai_member(api, org, workspaceId=other_dept["id"])
    f.add_team_member(api, org, team, member["id"])


def test_member_from_another_organization_returns_422(api, org, other_org, team):
    foreign = f.make_ai_member(api, other_org)
    response = api.post(f"/teams/{team['id']}/members", org, json={"memberId": foreign["id"]})
    assert_error(response, 422, "INVALID_REFERENCE", "memberId")


def test_list_team_members(api, org, team, ai_member):
    f.add_team_member(api, org, team, org.owner_id, "LEAD")
    f.add_team_member(api, org, team, ai_member["id"])
    items = api.get(f"/teams/{team['id']}/members", org).json()["items"]
    assert {(m["memberId"], m["role"]) for m in items} == {(org.owner_id, "LEAD"), (ai_member["id"], "MEMBER")}


def test_patch_team_member_role(api, org, team, ai_member):
    f.add_team_member(api, org, team, ai_member["id"])
    response = api.patch(f"/teams/{team['id']}/members/{ai_member['id']}", org, json={"role": "LEAD"})
    assert response.json()["role"] == "LEAD"


def test_remove_member_then_remove_again_returns_404(api, org, team, ai_member):
    f.add_team_member(api, org, team, ai_member["id"])
    path = f"/teams/{team['id']}/members/{ai_member['id']}"
    assert api.delete(path, org).status_code == 204
    assert_error(api.delete(path, org), 404, "TEAM_MEMBER_NOT_FOUND")
    assert api.get(f"/members/{ai_member['id']}", org).status_code == 200  # the member itself stays


def test_patch_unknown_team_member_returns_404(api, org, team, ai_member):
    response = api.patch(f"/teams/{team['id']}/members/{ai_member['id']}", org, json={"role": "LEAD"})
    assert_error(response, 404, "TEAM_MEMBER_NOT_FOUND")


def test_team_projects(api, org, workspace, team, project_with_team):
    f.make_project(api, org, workspace)  # a project this team is not on
    items = api.get(f"/teams/{team['id']}/projects", org).json()["items"]
    assert [p["id"] for p in items] == [project_with_team["id"]]
