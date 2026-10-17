import factories as f
from factories import assert_error


def test_create_workspace(api, org):
    response = api.post(
        f"/organizations/{org.id}/workspaces",
        json={"name": "Software", "description": "Eng", "createdById": org.owner_id},
    )
    assert response.status_code == 201
    body = response.json()
    assert (body["name"], body["description"], body["organizationId"]) == ("Software", "Eng", org.id)
    assert body["archivedAt"] is None and body["createdById"] == org.owner_id


def test_duplicate_name_in_another_case_returns_409(api, org):
    f.make_workspace(api, org, name="Software")
    response = api.post(
        f"/organizations/{org.id}/workspaces", json={"name": "SOFTWARE", "createdById": org.owner_id}
    )
    assert_error(response, 409, "WORKSPACE_ALREADY_EXISTS", "name")


def test_same_name_in_another_organization_is_allowed(api, org, other_org):
    f.make_workspace(api, org, name="Software")
    f.make_workspace(api, other_org, name="Software")


def test_name_of_an_archived_workspace_can_be_reused(api, org):
    old = f.make_workspace(api, org, name="Software")
    api.delete(f"/workspaces/{old['id']}", org)
    f.make_workspace(api, org, name="software")


def test_created_by_from_another_organization_returns_422(api, org, other_org):
    response = api.post(
        f"/organizations/{org.id}/workspaces",
        json={"name": "X", "createdById": other_org.owner_id},
    )
    assert_error(response, 422, "INVALID_REFERENCE", "createdById")


def test_blank_name_returns_422(api, org):
    response = api.post(
        f"/organizations/{org.id}/workspaces", json={"name": "  ", "createdById": org.owner_id}
    )
    assert_error(response, 422, "VALIDATION_ERROR", "name")


def test_get_and_patch(api, org, workspace):
    assert api.get(f"/workspaces/{workspace['id']}", org).json()["id"] == workspace["id"]
    response = api.patch(f"/workspaces/{workspace['id']}", org, json={"name": "Renamed", "description": "d"})
    assert (response.json()["name"], response.json()["description"]) == ("Renamed", "d")


def test_patch_to_a_taken_name_returns_409(api, org, workspace):
    other = f.make_workspace(api, org)
    response = api.patch(f"/workspaces/{workspace['id']}", org, json={"name": other["name"].upper()})
    assert_error(response, 409, "WORKSPACE_ALREADY_EXISTS", "name")


def test_delete_archives_and_patch_restores(api, org, workspace):
    path = f"/workspaces/{workspace['id']}"
    assert api.delete(path, org).status_code == 204
    assert api.get(path, org).json()["archivedAt"] is not None
    assert api.patch(path, org, json={"archived": False}).json()["archivedAt"] is None


def test_restoring_into_a_taken_name_returns_409(api, org, workspace):
    path = f"/workspaces/{workspace['id']}"
    api.delete(path, org)
    f.make_workspace(api, org, name=workspace["name"])
    assert_error(api.patch(path, org, json={"archived": False}), 409, "WORKSPACE_ALREADY_EXISTS", "name")


def test_list_with_archived_filter(api, org, workspace):
    archived = f.make_workspace(api, org)
    api.delete(f"/workspaces/{archived['id']}", org)
    path = f"/organizations/{org.id}/workspaces"

    def ids(**params):
        return {w["id"] for w in api.get(path, params=params).json()["items"]}

    assert ids() == {workspace["id"], archived["id"]}  # archived rows are not hidden
    assert ids(archived="true") == {archived["id"]}
    assert ids(archived="false") == {workspace["id"]}
