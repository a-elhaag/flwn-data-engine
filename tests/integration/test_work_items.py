import pytest

import factories as f
from factories import assert_error


def test_create_with_defaults(api, org, project):
    response = api.post(
        f"/projects/{project['id']}/work-items",
        org,
        json={"title": "Login", "type": "FEATURE", "createdById": org.owner_id},
    )
    assert response.status_code == 201
    body = response.json()
    assert (body["priority"], body["status"], body["type"]) == ("MEDIUM", "OPEN", "FEATURE")
    assert body["projectId"] == project["id"] and body["organizationId"] == org.id


def test_type_is_required(api, org, project):
    response = api.post(
        f"/projects/{project['id']}/work-items", org, json={"title": "Login", "createdById": org.owner_id}
    )
    assert_error(response, 422, "VALIDATION_ERROR", "type")


@pytest.mark.parametrize("value", ["EPIC", ""])
def test_invalid_type_returns_422(api, org, project, value):
    body = {"title": "Login", "type": value, "createdById": org.owner_id}
    assert_error(api.post(f"/projects/{project['id']}/work-items", org, json=body), 422, "VALIDATION_ERROR", "type")


def test_get_and_patch(api, org, work_item):
    path = f"/work-items/{work_item['id']}"
    response = api.patch(path, org, json={"title": "Renamed", "priority": "URGENT", "status": "IN_PROGRESS"})
    body = response.json()
    assert (body["title"], body["priority"], body["status"]) == ("Renamed", "URGENT", "IN_PROGRESS")
    assert api.get(path, org).json()["title"] == "Renamed"


def test_filters_status_type_and_priority(api, org, project):
    bug = f.make_work_item(api, org, project, type="BUG", priority="HIGH")
    story = f.make_work_item(api, org, project, type="STORY", status="DONE")
    path = f"/projects/{project['id']}/work-items"

    def ids(**params):
        return {w["id"] for w in api.get(path, org, params=params).json()["items"]}

    assert ids(type="BUG") == {bug["id"]}
    assert ids(type="STORY") == {story["id"]}
    assert ids(status="DONE") == {story["id"]}
    assert ids(status="OPEN") == {bug["id"]}
    assert ids(priority="HIGH") == {bug["id"]}
    assert ids(priority="MEDIUM") == {story["id"]}


def test_delete_keeps_its_tasks_with_work_item_id_null(api, org, project, work_item):
    task = f.make_task(api, org, project, workItemId=work_item["id"])
    assert api.delete(f"/work-items/{work_item['id']}", org).status_code == 204
    assert_error(api.get(f"/work-items/{work_item['id']}", org), 404, "WORK_ITEM_NOT_FOUND")
    kept = api.get(f"/tasks/{task['id']}", org).json()
    assert kept["workItemId"] is None and kept["projectId"] == project["id"]
