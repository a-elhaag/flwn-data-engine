import uuid

import pytest

import factories as f
from factories import assert_error


def test_create_project(api, org, workspace):
    response = api.post(
        f"/workspaces/{workspace['id']}/projects",
        org,
        json={"key": "FLWN", "name": "Flwn", "description": "d", "createdById": org.owner_id},
    )
    assert response.status_code == 201
    body = response.json()
    assert (body["key"], body["status"], body["workspaceId"], body["organizationId"]) == (
        "FLWN", "ACTIVE", workspace["id"], org.id,
    )  # fmt: skip
    assert "taskCounter" not in body and "task_counter" not in body


def test_project_in_unknown_workspace_returns_404(api, org):
    body = {"key": "FLWN", "name": "P", "createdById": org.owner_id}
    assert_error(api.post(f"/workspaces/{uuid.uuid4()}/projects", org, json=body), 404, "WORKSPACE_NOT_FOUND")


@pytest.mark.parametrize("key", ["flwn", "A", "ABCDEFGHIJK", "FL1N", "FL-N"])
def test_malformed_key_returns_422(api, org, workspace, key):
    body = {"key": key, "name": "P", "createdById": org.owner_id}
    response = api.post(f"/workspaces/{workspace['id']}/projects", org, json=body)
    assert_error(response, 422, "VALIDATION_ERROR", "key")


def test_duplicate_key_in_the_organization_returns_409(api, org, workspace):
    f.make_project(api, org, workspace, key="FLWN")
    other_workspace = f.make_workspace(api, org)
    body = {"key": "FLWN", "name": "Another", "createdById": org.owner_id}
    response = api.post(f"/workspaces/{other_workspace['id']}/projects", org, json=body)
    assert_error(response, 409, "PROJECT_KEY_ALREADY_EXISTS", "key")


def test_duplicate_key_is_rejected_even_if_the_other_project_is_archived(api, org, workspace):
    old = f.make_project(api, org, workspace, key="FLWN")
    api.delete(f"/projects/{old['id']}", org)
    body = {"key": "FLWN", "name": "Reborn", "createdById": org.owner_id}
    response = api.post(f"/workspaces/{workspace['id']}/projects", org, json=body)
    assert_error(response, 409, "PROJECT_KEY_ALREADY_EXISTS", "key")


def test_same_key_in_another_organization_is_allowed(api, org, other_org, workspace):
    f.make_project(api, org, workspace, key="FLWN")
    f.make_project(api, other_org, f.make_workspace(api, other_org), key="FLWN")


def test_duplicate_name_in_one_workspace_returns_409(api, org, workspace):
    f.make_project(api, org, workspace, name="Flwn")
    body = {"key": "OTHR", "name": "FLWN", "createdById": org.owner_id}
    response = api.post(f"/workspaces/{workspace['id']}/projects", org, json=body)
    assert_error(response, 409, "PROJECT_ALREADY_EXISTS", "name")


def test_patch_name_description_and_status(api, org, project):
    response = api.patch(
        f"/projects/{project['id']}", org, json={"name": "Renamed", "description": "d", "status": "COMPLETED"}
    )
    body = response.json()
    assert (body["name"], body["description"], body["status"]) == ("Renamed", "d", "COMPLETED")
    assert body["key"] == project["key"]


def test_patch_key_returns_422_because_it_is_immutable(api, org, project):
    response = api.patch(f"/projects/{project['id']}", org, json={"key": "NEWK"})
    assert_error(response, 422, "VALIDATION_ERROR", "key")


def test_archive_and_restore(api, org, project):
    path = f"/projects/{project['id']}"
    assert api.delete(path, org).status_code == 204
    assert api.get(path, org).json()["archivedAt"] is not None
    assert api.patch(path, org, json={"archived": False}).json()["archivedAt"] is None


def test_list_filters_status_and_archived(api, org, workspace, project):
    done = f.make_project(api, org, workspace, status="COMPLETED")
    archived = f.make_project(api, org, workspace)
    api.delete(f"/projects/{archived['id']}", org)
    path = f"/workspaces/{workspace['id']}/projects"

    def ids(**params):
        return {p["id"] for p in api.get(path, org, params=params).json()["items"]}

    assert ids() == {project["id"], done["id"], archived["id"]}
    assert ids(status="COMPLETED") == {done["id"]}
    assert ids(archived="true") == {archived["id"]}
    assert ids(archived="false") == {project["id"], done["id"]}
    assert ids(archived="false", status="ACTIVE") == {project["id"]}


# --- project teams ------------------------------------------------------------------------


def test_add_a_team_from_the_same_department(api, org, project, team):
    added = f.add_team_to_project(api, org, project, team)
    assert (added["projectId"], added["teamId"], added["organizationId"]) == (project["id"], team["id"], org.id)


def test_add_a_team_from_another_department_of_the_same_org(api, org, project):
    other_team = f.make_team(api, org, f.make_workspace(api, org, name="HR"))
    f.add_team_to_project(api, org, project, other_team)
    teams = api.get(f"/projects/{project['id']}/teams", org).json()["items"]
    assert [t["teamId"] for t in teams] == [other_team["id"]]


def test_team_from_another_organization_returns_422(api, org, other_org, project):
    foreign = f.make_team(api, other_org, f.make_workspace(api, other_org))
    response = api.post(f"/projects/{project['id']}/teams", org, json={"teamId": foreign["id"]})
    assert_error(response, 422, "INVALID_REFERENCE", "teamId")


def test_adding_the_same_team_twice_returns_409(api, org, project, team):
    f.add_team_to_project(api, org, project, team)
    response = api.post(f"/projects/{project['id']}/teams", org, json={"teamId": team["id"]})
    assert_error(response, 409, "PROJECT_TEAM_ALREADY_EXISTS", "teamId")


def test_remove_team_from_project(api, org, project_with_team, team):
    path = f"/projects/{project_with_team['id']}/teams/{team['id']}"
    assert api.delete(path, org).status_code == 204
    assert api.get(f"/projects/{project_with_team['id']}/teams", org).json()["items"] == []
    assert_error(api.delete(path, org), 404, "PROJECT_TEAM_NOT_FOUND")


def test_remove_team_that_still_has_sprints_returns_409(api, org, project_with_team, team, sprint):
    response = api.delete(f"/projects/{project_with_team['id']}/teams/{team['id']}", org)
    assert_error(response, 409, "PROJECT_TEAM_HAS_SPRINTS", "teamId")
    assert len(api.get(f"/projects/{project_with_team['id']}/teams", org).json()["items"]) == 1


def test_team_can_be_removed_after_its_sprints_are_deleted(api, org, project_with_team, team, sprint):
    api.delete(f"/sprints/{sprint['id']}", org)
    assert api.delete(f"/projects/{project_with_team['id']}/teams/{team['id']}", org).status_code == 204
