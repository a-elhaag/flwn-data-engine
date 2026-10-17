import uuid

import factories as f
from factories import assert_error


def test_create_sprint_under_a_project_team(api, org, project_with_team, team):
    response = api.post(
        f"/projects/{project_with_team['id']}/teams/{team['id']}/sprints",
        org,
        json={"name": "Sprint 1", "goal": "Ship", "startDate": "2026-02-01", "endDate": "2026-02-14",
              "createdById": org.owner_id},
    )  # fmt: skip
    assert response.status_code == 201
    body = response.json()
    assert (body["status"], body["teamId"], body["projectId"]) == ("PLANNED", team["id"], project_with_team["id"])
    assert (body["startDate"], body["endDate"]) == ("2026-02-01", "2026-02-14")


def test_team_not_on_the_project_returns_404(api, org, project, team):
    response = api.post(
        f"/projects/{project['id']}/teams/{team['id']}/sprints",
        org,
        json={"name": "S", "createdById": org.owner_id},
    )
    assert_error(response, 404, "PROJECT_TEAM_NOT_FOUND")


def test_end_before_start_on_create_returns_422(api, org, project_with_team, team):
    response = api.post(
        f"/projects/{project_with_team['id']}/teams/{team['id']}/sprints",
        org,
        json={"name": "S", "startDate": "2026-02-02", "endDate": "2026-02-01", "createdById": org.owner_id},
    )
    assert_error(response, 422, "VALIDATION_ERROR")


def test_end_before_start_on_patch_returns_422_on_end_date(api, org, project_with_team, team):
    sprint = f.make_sprint(api, org, project_with_team, team, startDate="2026-02-02")
    response = api.patch(f"/sprints/{sprint['id']}", org, json={"endDate": "2026-02-01"})
    assert_error(response, 422, "VALIDATION_ERROR", "endDate")


def test_second_active_sprint_for_same_team_returns_409(api, org, project_with_team, team):
    f.make_sprint(api, org, project_with_team, team, status="ACTIVE")
    response = api.post(
        f"/projects/{project_with_team['id']}/teams/{team['id']}/sprints",
        org,
        json={"name": "Another", "status": "ACTIVE", "createdById": org.owner_id},
    )
    assert_error(response, 409, "SPRINT_ALREADY_ACTIVE", "status")


def test_two_teams_can_each_have_an_active_sprint_in_one_project(api, org, workspace, project_with_team, team):
    second_team = f.make_team(api, org, workspace)
    f.add_team_to_project(api, org, project_with_team, second_team)
    f.make_sprint(api, org, project_with_team, team, status="ACTIVE")
    f.make_sprint(api, org, project_with_team, second_team, status="ACTIVE")


def test_patch_to_active_while_another_is_active_returns_409(api, org, project_with_team, team):
    f.make_sprint(api, org, project_with_team, team, status="ACTIVE")
    planned = f.make_sprint(api, org, project_with_team, team)
    response = api.patch(f"/sprints/{planned['id']}", org, json={"status": "ACTIVE"})
    assert_error(response, 409, "SPRINT_ALREADY_ACTIVE", "status")


def test_completing_an_active_sprint_frees_the_slot(api, org, project_with_team, team):
    first = f.make_sprint(api, org, project_with_team, team, status="ACTIVE")
    api.patch(f"/sprints/{first['id']}", org, json={"status": "COMPLETED"})
    f.make_sprint(api, org, project_with_team, team, status="ACTIVE")


def test_duplicate_name_for_the_same_team_returns_409(api, org, project_with_team, team):
    f.make_sprint(api, org, project_with_team, team, name="Sprint 1")
    response = api.post(
        f"/projects/{project_with_team['id']}/teams/{team['id']}/sprints",
        org,
        json={"name": "SPRINT 1", "createdById": org.owner_id},
    )
    assert_error(response, 409, "SPRINT_ALREADY_EXISTS", "name")


def test_same_name_for_another_team_is_allowed(api, org, workspace, project_with_team, team):
    second_team = f.make_team(api, org, workspace)
    f.add_team_to_project(api, org, project_with_team, second_team)
    f.make_sprint(api, org, project_with_team, team, name="Sprint 1")
    f.make_sprint(api, org, project_with_team, second_team, name="Sprint 1")


def test_get_and_patch(api, org, sprint):
    path = f"/sprints/{sprint['id']}"
    body = api.patch(path, org, json={"name": "Renamed", "goal": "g", "status": "ACTIVE"}).json()
    assert (body["name"], body["goal"], body["status"]) == ("Renamed", "g", "ACTIVE")
    assert api.patch(path, org, json={"goal": None}).json()["goal"] is None


def test_delete_sends_its_tasks_back_to_the_backlog(api, org, project_with_team, sprint):
    task = f.make_task(api, org, project_with_team, sprintId=sprint["id"])
    assert api.delete(f"/sprints/{sprint['id']}", org).status_code == 204
    assert_error(api.get(f"/sprints/{sprint['id']}", org), 404, "SPRINT_NOT_FOUND")
    kept = api.get(f"/tasks/{task['id']}", org).json()
    assert kept["sprintId"] is None and kept["projectId"] == project_with_team["id"]
    backlog = api.get(f"/projects/{project_with_team['id']}/tasks", org, params={"backlog": "true"}).json()
    assert [t["id"] for t in backlog["items"]] == [task["id"]]


def test_list_filters_team_id_and_status(api, org, workspace, project_with_team, team):
    second_team = f.make_team(api, org, workspace)
    f.add_team_to_project(api, org, project_with_team, second_team)
    first = f.make_sprint(api, org, project_with_team, team, status="ACTIVE")
    second = f.make_sprint(api, org, project_with_team, second_team)
    path = f"/projects/{project_with_team['id']}/sprints"

    def ids(**params):
        return {s["id"] for s in api.get(path, org, params=params).json()["items"]}

    assert ids() == {first["id"], second["id"]}
    assert ids(teamId=team["id"]) == {first["id"]}
    assert ids(status="PLANNED") == {second["id"]}
    assert ids(teamId=second_team["id"], status="ACTIVE") == set()


def test_sprints_of_unknown_project_return_404(api, org):
    assert_error(api.get(f"/projects/{uuid.uuid4()}/sprints", org), 404, "PROJECT_NOT_FOUND")
