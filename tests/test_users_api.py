import uuid

from harness import CrudHarness

from app.api import tokens as t


class UsersApiTests(CrudHarness):
    def email(self):
        return f"{uuid.uuid4().hex[:10]}@example.com"

    def make(self, **body):
        response = self.client.post("/users", json={"email": self.email(), "name": "Ada", **body})
        self.assertEqual(response.status_code, 201, response.text)
        self.addCleanup(self.sql, "delete from users where id = :i", i=response.json()["id"])
        return response.json()

    def test_user_crud_and_soft_delete(self):
        user = self.make(settings={"theme": "dark"})
        self.assertEqual(user["settings"], {"theme": "dark"})
        updated = self.client.patch(f"/users/{user['id']}", json={"name": "Ada Byron"})
        self.assertEqual((updated.status_code, updated.json()["name"]), (200, "Ada Byron"))
        self.assertIn(user["id"], [u["id"] for u in self.client.get("/users").json()["items"]])
        self.assertEqual(self.client.delete(f"/users/{user['id']}").status_code, 204)
        self.assertEqual(self.client.get(f"/users/{user['id']}").status_code, 404)

    def test_email_is_unique_ignoring_case_and_validated(self):
        user = self.make()
        clash = self.client.post("/users", json={"email": user["email"].upper(), "name": "Other"})
        self.assertEqual(clash.status_code, 409, clash.text)
        bad = self.client.post("/users", json={"email": "not-an-email", "name": "X"})
        self.assertEqual(bad.status_code, 422)
        other = self.make()
        taken = self.client.patch(f"/users/{other['id']}", json={"email": user["email"]})
        self.assertEqual(taken.status_code, 409)
        self.assertEqual(self.client.patch(f"/users/{user['id']}", json={}).status_code, 422)

    def test_user_routes_are_backend_only(self):
        user = self.make()
        admin = self.token_headers(self.team_a, t.SCOPE_ADMIN)
        for method, path in (("get", "/users"), ("get", f"/users/{user['id']}")):
            response = getattr(self.client, method)(path, headers=admin)
            self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(self.client.get("/users", headers=self.NO_TOKEN).status_code, 401)
