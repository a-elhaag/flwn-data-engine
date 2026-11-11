"""Workspace settings are two sections: `engine` (checked on create and update) and `product`
(stored as given). Any other top-level key is refused with invalid_setting_key."""

import uuid

from harness import CrudHarness


class WorkspaceSettingsTests(CrudHarness):
    def owner(self) -> str:
        (user,) = self.sql(
            "insert into users (email, name) values (:e, 'Owner') returning id",
            e=f"{uuid.uuid4().hex[:10]}@example.com",
        )
        self.addCleanup(self.sql, "delete from users where id = :i", i=user["id"])
        self.addCleanup(self.sql, "delete from workspaces where created_by = :i", i=user["id"])
        return str(user["id"])

    def create(self, settings):
        return self.client.post(
            "/workspaces",
            json={
                "name": "Settings",
                "slug": f"settings-{uuid.uuid4().hex[:8]}",
                "owner_user_id": self.owner(),
                "settings": settings,
            },
        )

    def test_unknown_top_level_keys_are_refused(self):
        for bad in ({"theme": "dark"}, {"engine": {}, "recording_retention_days": 90}):
            response = self.create(bad)
            self.assertEqual(response.status_code, 422, (bad, response.text))
            self.assertEqual(response.json()["code"], "invalid_setting_key")

    def test_unknown_engine_keys_are_refused(self):
        response = self.create({"engine": {"retention": 5}})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["code"], "invalid_setting_key")

    def test_wrong_engine_values_are_refused_naming_the_key(self):
        for key, bad in (
            ("recording_retention_days", 0),
            ("recording_retention_days", 3651),
            ("recording_retention_days", "90"),
            ("recording_retention_days", True),
            ("recording_retention_days", 90.5),
            ("dev_session", "yes"),
            ("dev_session", 1),
        ):
            response = self.create({"engine": {key: bad}})
            self.assertEqual(response.status_code, 422, (key, bad, response.text))
            self.assertEqual(response.json()["code"], "invalid_setting_value")
            self.assertIn(key, response.text)

    def test_valid_engine_and_product_sections_are_stored_as_given(self):
        settings = {
            "engine": {"recording_retention_days": 3650, "dev_session": False},
            "product": {"theme": {"x": 1}},
        }
        response = self.create(settings)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["settings"], settings)

    def test_engine_and_product_are_checked_on_update(self):
        workspace = self.create({}).json()["id"]
        path = f"/workspaces/{workspace}"
        self.assertEqual(
            self.client.patch(
                path, json={"settings": {"engine": {"recording_retention_days": 0}}}
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.patch(
                path, json={"settings": {"engine": {"dev_session": "true"}}}
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.patch(path, json={"settings": {"flat": 1}}).json()["code"],
            "invalid_setting_key",
        )
        ok = self.client.patch(path, json={"settings": {"engine": {"recording_retention_days": 1}}})
        self.assertEqual(
            (ok.status_code, ok.json()["settings"]),
            (200, {"engine": {"recording_retention_days": 1}}),
        )
