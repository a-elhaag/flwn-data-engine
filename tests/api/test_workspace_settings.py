"""Engine-owned workspace settings are type-checked on create and update; other keys pass through."""

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

    def test_engine_keys_are_checked_on_create(self):
        for bad in (
            {"recording_retention_days": 0},
            {"recording_retention_days": 3651},
            {"recording_retention_days": "90"},
            {"recording_retention_days": True},
            {"recording_retention_days": 90.5},
            {"dev_session": "yes"},
            {"dev_session": 1},
        ):
            response = self.create(bad)
            self.assertEqual(response.status_code, 422, (bad, response.text))

    def test_valid_engine_keys_and_unknown_keys_are_stored_as_given(self):
        response = self.create(
            {"recording_retention_days": 3650, "dev_session": False, "theme": {"x": 1}}
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(
            response.json()["settings"],
            {"recording_retention_days": 3650, "dev_session": False, "theme": {"x": 1}},
        )

    def test_engine_keys_are_checked_on_update(self):
        workspace = self.create({}).json()["id"]
        path = f"/workspaces/{workspace}"
        self.assertEqual(
            self.client.patch(path, json={"settings": {"recording_retention_days": 0}}).status_code,
            422,
        )
        self.assertEqual(
            self.client.patch(path, json={"settings": {"dev_session": "true"}}).status_code, 422
        )
        ok = self.client.patch(path, json={"settings": {"recording_retention_days": 1}})
        self.assertEqual(
            (ok.status_code, ok.json()["settings"]), (200, {"recording_retention_days": 1})
        )
