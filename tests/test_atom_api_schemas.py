import unittest
from datetime import UTC, datetime

from pydantic import ValidationError

from app.api.atom_schemas import ConfigureAtom, ConnectionBody, ScheduleBody


class AtomRequestTests(unittest.TestCase):
    def test_schedule_requires_one_cadence_and_aware_time(self):
        for body in (
            {},
            {"cron": "* * * * *", "interval_minutes": 5},
            {"interval_minutes": 4},
            {"interval_minutes": 5, "next_run_at": "2026-01-01"},
        ):
            with self.subTest(body=body), self.assertRaises(ValidationError):
                ScheduleBody(**body)
        self.assertEqual(ScheduleBody(interval_minutes=5).timezone, "UTC")
        ScheduleBody(cron="0 * * * *", next_run_at=datetime.now(UTC))

    def test_connection_requires_explicit_identity_and_pinned_version(self):
        valid = dict(
            toolkit="github",
            composio_user_id="workspace:test",
            toolkit_version="20260101_01",
            permission_ceiling="read",
        )
        for patch in (
            {"toolkit_version": "latest"},
            {"toolkit_version": "LATEST"},
            {"composio_user_id": " "},
            {"status": "active"},
            {"allowed_tools": [""]},
            {"oauth_token": "not-accepted"},
        ):
            with self.subTest(patch=patch), self.assertRaises(ValidationError):
                ConnectionBody(**(valid | patch))
        ConnectionBody(**valid)
        ConnectionBody(**valid, status="active", composio_account_ref="account")

    def test_control_patch_cannot_set_identity_or_activate(self):
        for body in (
            {"status": "active"},
            {"member_id": "anything"},
            {"max_cost_per_day": None},
            {"name": None},
            {"owner_member_id": None},
        ):
            with self.subTest(body=body), self.assertRaises(ValidationError):
                ConfigureAtom(**body)
        self.assertEqual(
            ConfigureAtom(description=None).model_dump(exclude_unset=True), {"description": None}
        )
