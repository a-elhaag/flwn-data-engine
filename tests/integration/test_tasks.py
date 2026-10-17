import threading
import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient

import factories as f
from config import settings
from factories import assert_error


def test_numbers_and_keys_count_up_from_one(api, org, workspace):
    project = f.make_project(api, org, workspace, key="FLWN")
    tasks = [f.make_task(api, org, project) for _ in range(3)]
    assert [(t["number"], t["key"]) for t in tasks] == [(1, "FLWN-1"), (2, "FLWN-2"), (3, "FLWN-3")]


def test_numbers_are_per_project(api, org, workspace):
    first, second = f.make_project(api, org, workspace), f.make_project(api, org, workspace)
    f.make_task(api, org, first)
    assert f.make_task(api, org, second)["number"] == 1


def test_numbers_are_never_reused_after_a_delete(api, org, project):
    f.make_task(api, org, project)
    last = f.make_task(api, org, project)
    assert api.delete(f"/tasks/{last['id']}", org).status_code == 204
    assert f.make_task(api, org, project)["number"] == 3


def test_failed_create_rolls_the_counter_back(api, org, project):
    f.make_task(api, org, project)
    body = {"title": "Bad", "createdById": org.owner_id, "sprintId": str(uuid.uuid4())}
    assert_error(api.post(f"/projects/{project['id']}/tasks", org, json=body), 422, "INVALID_REFERENCE", "sprintId")
    assert f.make_task(api, org, project)["number"] == 2


def test_concurrent_creates_get_distinct_contiguous_numbers(app, api, org, project):
    results, errors = [], []

    def worker(i):
        client = f.Api(TestClient(app, headers={"X-Data-API-Key": settings.DATA_API_KEY}))
        response = client.post(
            f"/projects/{project['id']}/tasks", org, json={"title": f"t{i}", "createdById": org.owner_id}
        )
        (results if response.status_code == 201 else errors).append(
            response.json()["number"] if response.status_code == 201 else response.text
        )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    assert sorted(results) == list(range(1, 11))


def test_task_in_unknown_project_returns_404(api, org):
    body = {"title": "T", "createdById": org.owner_id}
    assert_error(api.post(f"/projects/{uuid.uuid4()}/tasks", org, json=body), 404, "PROJECT_NOT_FOUND")


def test_defaults_are_todo_and_medium(api, org, project):
    task = f.make_task(api, org, project)
    assert (task["status"], task["priority"]) == ("TODO", "MEDIUM")
    assert task["completedAt"] is None and task["sprintId"] is None and task["assigneeId"] is None


def test_done_without_completed_at_on_create_returns_422(api, org, project):
    body = {"title": "T", "createdById": org.owner_id, "status": "DONE"}
    assert_error(api.post(f"/projects/{project['id']}/tasks", org, json=body), 422, "VALIDATION_ERROR")


def test_completed_at_without_done_on_create_returns_422(api, org, project):
    body = {"title": "T", "createdById": org.owner_id, "completedAt": "2026-01-01T00:00:00Z"}
    assert_error(api.post(f"/projects/{project['id']}/tasks", org, json=body), 422, "VALIDATION_ERROR")


def test_done_with_completed_at_on_create_is_accepted(api, org, project):
    task = f.make_task(api, org, project, status="DONE", completedAt="2026-01-01T00:00:00Z")
    assert task["status"] == "DONE" and task["completedAt"]


def test_patch_to_done_without_completed_at_returns_422_on_completed_at(api, org, task):
    response = api.patch(f"/tasks/{task['id']}", org, json={"status": "DONE"})
    assert_error(response, 422, "VALIDATION_ERROR", "completedAt")


def test_patch_to_done_with_completed_at_works(api, org, task):
    body = {"status": "DONE", "completedAt": "2026-10-17T10:00:00Z"}
    assert api.patch(f"/tasks/{task['id']}", org, json=body).json()["status"] == "DONE"


def test_reopening_a_done_task_requires_clearing_completed_at(api, org, project):
    task = f.make_task(api, org, project, status="DONE", completedAt="2026-01-01T00:00:00Z")
    path = f"/tasks/{task['id']}"
    assert_error(api.patch(path, org, json={"status": "TODO"}), 422, "VALIDATION_ERROR", "completedAt")
    reopened = api.patch(path, org, json={"status": "TODO", "completedAt": None}).json()
    assert (reopened["status"], reopened["completedAt"]) == ("TODO", None)


def test_sprint_of_another_project_returns_422(api, org, workspace, project, team):
    other_project = f.make_project(api, org, workspace)
    f.add_team_to_project(api, org, other_project, team)
    foreign_sprint = f.make_sprint(api, org, other_project, team)
    body = {"title": "T", "createdById": org.owner_id, "sprintId": foreign_sprint["id"]}
    assert_error(api.post(f"/projects/{project['id']}/tasks", org, json=body), 422, "INVALID_REFERENCE", "sprintId")


def test_work_item_of_another_project_returns_422(api, org, workspace, project):
    foreign = f.make_work_item(api, org, f.make_project(api, org, workspace))
    body = {"title": "T", "createdById": org.owner_id, "workItemId": foreign["id"]}
    assert_error(api.post(f"/projects/{project['id']}/tasks", org, json=body), 422, "INVALID_REFERENCE", "workItemId")


def test_assignee_from_another_organization_returns_422(api, org, other_org, project):
    foreign = f.make_ai_member(api, other_org)
    body = {"title": "T", "createdById": org.owner_id, "assigneeId": foreign["id"]}
    assert_error(api.post(f"/projects/{project['id']}/tasks", org, json=body), 422, "INVALID_REFERENCE", "assigneeId")


def test_assignee_not_on_any_project_team_is_accepted(api, org, project, ai_member):
    """Business rule lives in the backend; the data engine only enforces same-organization."""
    task = f.make_task(api, org, project, assigneeId=ai_member["id"])
    assert task["assigneeId"] == ai_member["id"]


def test_created_by_from_another_organization_returns_422(api, org, other_org, project):
    body = {"title": "T", "createdById": other_org.owner_id}
    assert_error(api.post(f"/projects/{project['id']}/tasks", org, json=body), 422, "INVALID_REFERENCE", "createdById")


def test_naive_due_date_returns_422(api, org, project):
    body = {"title": "T", "createdById": org.owner_id, "dueDate": "2026-11-03T17:00:00"}
    assert_error(api.post(f"/projects/{project['id']}/tasks", org, json=body), 422, "VALIDATION_ERROR", "dueDate")


def test_due_date_with_offset_is_stored_as_the_same_utc_instant(api, org, project):
    task = f.make_task(api, org, project, dueDate="2026-11-03T17:00:00+03:00")
    stored = datetime.fromisoformat(task["dueDate"])
    assert stored == datetime(2026, 11, 3, 14, 0, tzinfo=timezone.utc)
    again = api.get(f"/tasks/{task['id']}", org).json()
    assert datetime.fromisoformat(again["dueDate"]) == stored


# --- reading ------------------------------------------------------------------------------


def test_by_key_finds_the_task(api, org, workspace):
    project = f.make_project(api, org, workspace, key="FLWN")
    f.make_task(api, org, project)
    second = f.make_task(api, org, project)
    found = api.get("/tasks/by-key/FLWN-2", org)
    assert found.status_code == 200 and found.json()["id"] == second["id"]


def test_by_key_in_another_organization_returns_404(api, org, other_org, workspace):
    f.make_task(api, org, f.make_project(api, org, workspace, key="FLWN"))
    assert_error(api.get("/tasks/by-key/FLWN-1", other_org), 404, "TASK_NOT_FOUND")


def test_by_key_with_the_same_key_in_two_orgs_returns_each_orgs_own_task(api, org, other_org, workspace):
    mine = f.make_task(api, org, f.make_project(api, org, workspace, key="FLWN"))
    theirs = f.make_task(api, other_org, f.make_project(api, other_org, f.make_workspace(api, other_org), key="FLWN"))
    assert api.get("/tasks/by-key/FLWN-1", org).json()["id"] == mine["id"]
    assert api.get("/tasks/by-key/FLWN-1", other_org).json()["id"] == theirs["id"]


def test_by_key_with_bad_format_returns_422(api, org):
    assert_error(api.get("/tasks/by-key/flwn-1", org), 422, "VALIDATION_ERROR", "key")


def test_by_key_unknown_number_returns_404(api, org, workspace):
    f.make_task(api, org, f.make_project(api, org, workspace, key="FLWN"))
    assert_error(api.get("/tasks/by-key/FLWN-99", org), 404, "TASK_NOT_FOUND")


def test_get_task_returns_its_key(api, org, task, project):
    assert api.get(f"/tasks/{task['id']}", org).json()["key"] == f"{project['key']}-1"


def test_list_filters(api, org, project_with_team, team, ai_member):
    sprint = f.make_sprint(api, org, project_with_team, team)
    work_item = f.make_work_item(api, org, project_with_team)
    in_sprint = f.make_task(api, org, project_with_team, sprintId=sprint["id"], priority="HIGH")
    assigned = f.make_task(api, org, project_with_team, assigneeId=ai_member["id"], status="BLOCKED")
    grouped = f.make_task(api, org, project_with_team, workItemId=work_item["id"])
    plain = f.make_task(api, org, project_with_team)
    path = f"/projects/{project_with_team['id']}/tasks"

    def ids(**params):
        return {t["id"] for t in api.get(path, org, params=params).json()["items"]}

    assert ids(status="BLOCKED") == {assigned["id"]}
    assert ids(priority="HIGH") == {in_sprint["id"]}
    assert ids(assigneeId=ai_member["id"]) == {assigned["id"]}
    assert ids(sprintId=sprint["id"]) == {in_sprint["id"]}
    assert ids(workItemId=work_item["id"]) == {grouped["id"]}
    assert ids(backlog="true") == {assigned["id"], grouped["id"], plain["id"]}
    assert ids(backlog="true", status="TODO") == {grouped["id"], plain["id"]}
    assert len(ids()) == 4


def test_list_is_ordered_by_number(api, org, project):
    for _ in range(5):
        f.make_task(api, org, project)
    numbers = [t["number"] for t in api.get(f"/projects/{project['id']}/tasks", org).json()["items"]]
    assert numbers == sorted(numbers) == [1, 2, 3, 4, 5]


def test_list_of_unknown_project_returns_404(api, org):
    assert_error(api.get(f"/projects/{uuid.uuid4()}/tasks", org), 404, "PROJECT_NOT_FOUND")


# --- updating and deleting ---------------------------------------------------------------


def test_patch_moves_a_task_into_a_sprint(api, org, project_with_team, sprint):
    task = f.make_task(api, org, project_with_team)
    moved = api.patch(f"/tasks/{task['id']}", org, json={"sprintId": sprint["id"]}).json()
    assert moved["sprintId"] == sprint["id"] and moved["key"] == task["key"]


def test_patch_assignee_null_unassigns(api, org, project, ai_member):
    task = f.make_task(api, org, project, assigneeId=ai_member["id"])
    unassigned = api.patch(f"/tasks/{task['id']}", org, json={"assigneeId": None}).json()
    assert unassigned["assigneeId"] is None


def test_patch_only_changes_the_sent_fields(api, org, project):
    task = f.make_task(api, org, project, description="keep me", priority="HIGH")
    patched = api.patch(f"/tasks/{task['id']}", org, json={"title": "New title"}).json()
    assert (patched["title"], patched["description"], patched["priority"]) == ("New title", "keep me", "HIGH")
    assert patched["number"] == task["number"]


def test_patch_with_an_empty_body_is_a_no_op(api, org, task):
    assert api.patch(f"/tasks/{task['id']}", org, json={}).json() == task


def test_patch_cannot_change_immutable_fields(api, org, task):
    for body in ({"createdById": task["createdById"]}, {"projectId": task["projectId"]}, {"number": 9}):
        assert_error(api.patch(f"/tasks/{task['id']}", org, json=body), 422, "VALIDATION_ERROR")


def test_delete_returns_204_and_the_task_is_gone(api, org, task):
    assert api.delete(f"/tasks/{task['id']}", org).status_code == 204
    assert_error(api.get(f"/tasks/{task['id']}", org), 404, "TASK_NOT_FOUND")
    assert_error(api.delete(f"/tasks/{task['id']}", org), 404, "TASK_NOT_FOUND")
