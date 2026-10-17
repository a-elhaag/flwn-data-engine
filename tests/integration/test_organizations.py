import uuid

from sqlalchemy import text

import factories as f
from db.session import get_engine
from factories import assert_error


def _count(table: str) -> int:
    with get_engine().connect() as conn:
        return conn.execute(text(f"SELECT count(*) FROM {table}")).scalar()


def test_create_returns_organization_and_its_owner_member(api, user):
    response = api.post("/organizations", json={"name": "Flwn", "createdById": user["id"]})
    assert response.status_code == 201
    body = response.json()
    assert body["organization"]["name"] == "Flwn"
    member = body["member"]
    assert (member["type"], member["role"], member["status"]) == ("HUMAN", "OWNER", "ACTIVE")
    assert (member["name"], member["email"]) == (user["name"], user["email"])
    assert member["organizationId"] == body["organization"]["id"]
    assert member["userId"] == user["id"]
    assert member["createdById"] is None and member["workspaceId"] is None


def test_creator_member_role_can_be_chosen(api, user):
    body = {"name": "Flwn", "createdById": user["id"], "creatorMember": {"role": "MEMBER"}}
    assert api.post("/organizations", json=body).json()["member"]["role"] == "MEMBER"


def test_creator_member_omitted_defaults_to_owner(api, user):
    body = api.post("/organizations", json={"name": "Flwn", "createdById": user["id"]}).json()
    assert body["member"]["role"] == "OWNER"


def test_unknown_creator_returns_422_and_saves_nothing(api):
    before = _count("organizations"), _count("members")
    response = api.post("/organizations", json={"name": "Ghost", "createdById": str(uuid.uuid4())})
    assert_error(response, 422, "INVALID_REFERENCE", "createdById")
    assert (_count("organizations"), _count("members")) == before


def test_blank_name_returns_422(api, user):
    response = api.post("/organizations", json={"name": "  ", "createdById": user["id"]})
    assert_error(response, 422, "VALIDATION_ERROR", "name")


def test_get_and_patch(api, org):
    assert api.get(f"/organizations/{org.id}").json()["id"] == org.id
    response = api.patch(f"/organizations/{org.id}", json={"name": "Renamed", "description": "d"})
    assert (response.json()["name"], response.json()["description"]) == ("Renamed", "d")


def test_patch_description_null_clears_it(api, org):
    api.patch(f"/organizations/{org.id}", json={"description": "d"})
    response = api.patch(f"/organizations/{org.id}", json={"description": None})
    assert response.json()["description"] is None


def test_delete_archives_instead_of_removing(api, org):
    assert api.delete(f"/organizations/{org.id}").status_code == 204
    after = api.get(f"/organizations/{org.id}")
    assert after.status_code == 200 and after.json()["archivedAt"] is not None


def test_patch_archived_false_restores(api, org):
    api.delete(f"/organizations/{org.id}")
    restored = api.patch(f"/organizations/{org.id}", json={"archived": False}).json()
    assert restored["archivedAt"] is None


def test_patch_archived_true_archives(api, org):
    assert api.patch(f"/organizations/{org.id}", json={"archived": True}).json()["archivedAt"]


def test_get_unknown_organization_returns_404(api):
    assert_error(api.get(f"/organizations/{uuid.uuid4()}"), 404, "ORGANIZATION_NOT_FOUND")
