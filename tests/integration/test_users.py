import uuid

import pytest

import factories as f
from factories import assert_error


def test_create_user_returns_201_and_lowercases_the_email(api):
    response = api.post("/users", json={"name": "Ahmed", "email": "Ahmed@Flwn.IO"})
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "ahmed@flwn.io" and body["name"] == "Ahmed" and body["id"]


def test_duplicate_email_in_another_case_returns_409(api):
    f.make_user(api, email="dup@flwn.io")
    response = api.post("/users", json={"name": "Other", "email": "DUP@flwn.io"})
    assert_error(response, 409, "USER_ALREADY_EXISTS", "email")


def test_find_by_email_ignores_input_case(api):
    created = f.make_user(api, email="find.me@flwn.io")
    page = api.get("/users", params={"email": "FIND.Me@FLWN.io"}).json()
    assert [u["id"] for u in page["items"]] == [created["id"]]


def test_find_by_unknown_email_returns_an_empty_page(api):
    page = api.get("/users", params={"email": "nobody@flwn.io"}).json()
    assert page["items"] == [] and page["hasMore"] is False


def test_find_without_email_returns_422(api):
    assert_error(api.get("/users"), 422, "VALIDATION_ERROR", "email")


def test_get_unknown_user_returns_404(api):
    assert_error(api.get(f"/users/{uuid.uuid4()}"), 404, "USER_NOT_FOUND")


def test_patch_name_and_email(api, user):
    response = api.patch(f"/users/{user['id']}", json={"name": "New Name", "email": "New@Flwn.io"})
    assert response.status_code == 200
    assert (response.json()["name"], response.json()["email"]) == ("New Name", "new@flwn.io")


def test_patch_to_an_existing_email_returns_409(api, user):
    other = f.make_user(api)
    response = api.patch(f"/users/{user['id']}", json={"email": other["email"].upper()})
    assert_error(response, 409, "USER_ALREADY_EXISTS", "email")


def test_empty_patch_is_a_no_op(api, user):
    response = api.patch(f"/users/{user['id']}", json={})
    assert response.status_code == 200 and response.json() == user


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"name": "A", "email": "not-an-email"}, "email"),
        ({"name": "   ", "email": "a@flwn.io"}, "name"),
        ({"name": "A"}, "email"),
    ],
)
def test_invalid_create_body_returns_422(api, body, field):
    assert_error(api.post("/users", json=body), 422, "VALIDATION_ERROR", field)


def test_memberships_list_one_member_per_organization(api, user):
    org_a = f.make_org(api, user)
    org_b = f.make_org(api, user)
    page = api.get(f"/users/{user['id']}/memberships").json()
    assert {m["organizationId"] for m in page["items"]} == {org_a.id, org_b.id}
    assert all(m["userId"] == user["id"] and m["name"] == user["name"] for m in page["items"])


def test_memberships_of_unknown_user_returns_404(api):
    assert_error(api.get(f"/users/{uuid.uuid4()}/memberships"), 404, "USER_NOT_FOUND")


def test_users_are_global_and_need_no_organization(api, user):
    assert api.get(f"/users/{user['id']}").status_code == 200
    assert api.get(f"/users/{user['id']}/memberships").status_code == 200
