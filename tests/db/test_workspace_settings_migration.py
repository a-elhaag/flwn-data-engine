"""Migration 0014 (workspace settings split): flat settings move under `product`, engine keys move
to `engine`, an empty object stays empty, and a row already split is left alone."""

import json
import os
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import db_support

try:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    IMPORT_ERROR = None
except ImportError as exc:  # alembic / sqlalchemy not installed
    IMPORT_ERROR = exc

ROOT = Path(__file__).resolve().parents[2]


def setUpModule():
    if IMPORT_ERROR:
        raise unittest.SkipTest(f"migration tests need alembic and sqlalchemy: {IMPORT_ERROR}")


class WorkspaceSettingsMigrationTest(unittest.TestCase):
    def fresh(self):
        installed = db_support.engine()
        admin = db_support._state["admin"]
        name = f"flwn_settings_{uuid.uuid4().hex[:8]}"
        with admin.connect() as conn:
            conn.execute(text(f'create database "{name}"'))
        self.addCleanup(self._drop, admin, name)
        url = make_url(installed.url.render_as_string(hide_password=False)).set(database=name)
        url = url.render_as_string(hide_password=False)
        cfg = Config(str(ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT / "migrations"))
        env = patch.dict(os.environ, {"DATABASE_ADMIN_URL": url, "DATABASE_URL": url})
        env.start()
        self.addCleanup(env.stop)
        command.upgrade(cfg, "0013")
        engine = create_engine(url)
        self.addCleanup(engine.dispose)
        return cfg, engine

    @staticmethod
    def _drop(admin, name):
        with admin.connect() as conn:
            conn.execute(text(f'drop database "{name}" with (force)'))

    def test_settings_are_split_into_engine_and_product(self):
        cfg, engine = self.fresh()
        before = {
            "flat": {"theme": "dark", "dev_session": True, "recording_retention_days": 90},
            "plain": {"theme": "light"},
            "empty": {},
            "split": {"engine": {"dev_session": True}, "product": {"x": 1}},
        }
        ids = {}
        with engine.begin() as conn:
            for key, value in before.items():
                ids[key] = conn.execute(
                    text(
                        "insert into workspaces (name, slug, settings) "
                        "values (:n, :s, cast(:j as jsonb)) returning id"
                    ),
                    {"n": key, "s": f"{key}-{uuid.uuid4().hex[:6]}", "j": json.dumps(value)},
                ).scalar_one()

        command.upgrade(cfg, "0014")

        with engine.connect() as conn:
            after = {
                key: conn.scalar(text("select settings from workspaces where id = :i"), {"i": wid})
                for key, wid in ids.items()
            }
        self.assertEqual(
            after["flat"],
            {
                "product": {"theme": "dark"},
                "engine": {"dev_session": True, "recording_retention_days": 90},
            },
        )
        self.assertEqual(after["plain"], {"product": {"theme": "light"}})
        self.assertEqual(after["empty"], {})
        self.assertEqual(after["split"], before["split"])


if __name__ == "__main__":
    unittest.main()
