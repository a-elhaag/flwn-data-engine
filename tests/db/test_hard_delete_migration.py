"""Migration 0012 (hard delete): it purges the soft-deleted rows, keeps the live ones, and refuses
when a live workspace would lose its only active owner."""

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


class HardDeleteMigrationTest(unittest.TestCase):
    def fresh(self, revision="0011"):
        installed = db_support.engine()
        admin = db_support._state["admin"]
        name = f"flwn_hard_{uuid.uuid4().hex[:8]}"
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
        command.upgrade(cfg, revision)
        engine = create_engine(url)
        self.addCleanup(engine.dispose)
        return cfg, engine

    @staticmethod
    def _drop(admin, name):
        with admin.connect() as conn:
            conn.execute(text(f'drop database "{name}" with (force)'))

    def seed(self, engine):
        """A live workspace and a soft-deleted one; live and removed users, members, teams."""
        with engine.begin() as conn:
            live = conn.execute(
                text("insert into workspaces (name, slug) values ('Live', 'live') returning id")
            ).scalar_one()
            gone = conn.execute(
                text(
                    "insert into workspaces (name, slug, deleted_at) "
                    "values ('Gone', 'gone', now()) returning id"
                )
            ).scalar_one()
            keep_user = conn.execute(
                text(
                    "insert into users (email, name) values ('keep@example.com', 'Keep') returning id"
                )
            ).scalar_one()
            gone_user = conn.execute(
                text(
                    "insert into users (email, name, deleted_at) "
                    "values ('gone@example.com', 'Gone', now()) returning id"
                )
            ).scalar_one()
            conn.execute(
                text(
                    "insert into members (workspace_id, user_id, type, role) "
                    "values (:w, :u, 'HUMAN', 'owner')"
                ),
                {"w": live, "u": keep_user},
            )
            conn.execute(
                text(
                    "insert into members (workspace_id, user_id, type, role) "
                    "values (:w, :u, 'HUMAN', 'owner')"  # a second owner: the first stays live
                ),
                {"w": live, "u": gone_user},
            )
            removed_member = conn.execute(
                text(
                    "insert into members (workspace_id, type, role, deleted_at, name, agent_kind) "
                    "values (:w, 'AI', 'member', now(), 'Old', 'security') returning id"
                ),
                {"w": live},
            ).scalar_one()
            team = conn.execute(
                text(
                    "insert into teams (workspace_id, key, name, deleted_at) "
                    "values (:w, 'OLD', 'Old', now()) returning id"
                ),
                {"w": live},
            ).scalar_one()
            project = conn.execute(
                text(
                    "insert into projects (workspace_id, key, name, team_id, lead_id) "
                    "values (:w, 'LIVE', 'Live', :t, :m) returning id"
                ),
                {"w": live, "t": team, "m": removed_member},
            ).scalar_one()
            # A soft-deleted atom: its identity and version go too.
            atom_member = conn.execute(
                text(
                    "insert into members (workspace_id, type, role, name, agent_kind) "
                    "values (:w, 'AI', 'member', 'Bot', 'atom') returning id"
                ),
                {"w": live},
            ).scalar_one()
            conn.execute(
                text(
                    "insert into atoms (workspace_id, member_id, kind, name, model_tier, "
                    "max_runs_per_day, max_cost_per_day, max_actions_per_day, deleted_at) "
                    "values (:w, :m, 'workspace', 'Bot', 'small', 1, 1, 1, now())"
                ),
                {"w": live, "m": atom_member},
            )
        return {"live": live, "gone": gone, "project": project, "atom_member": atom_member}

    def test_purges_soft_deleted_rows_and_keeps_live_ones(self):
        cfg, engine = self.fresh()
        seeded = self.seed(engine)
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            self.assertIsNone(
                conn.scalar(
                    text(
                        "select 1 from information_schema.columns where column_name = 'deleted_at'"
                    )
                )
            )
            self.assertIsNone(
                conn.scalar(text("select id from workspaces where id = :g"), {"g": seeded["gone"]})
            )
            self.assertEqual(
                conn.scalar(text("select count(*) from users where email = 'gone@example.com'")), 0
            )
            self.assertEqual(
                conn.scalar(text("select count(*) from users where email = 'keep@example.com'")), 1
            )
            self.assertEqual(
                conn.scalar(
                    text("select count(*) from members where workspace_id = :w"),
                    {"w": seeded["live"]},
                ),
                1,
            )
            project = conn.execute(
                text("select team_id, lead_id from projects where id = :p"),
                {"p": seeded["project"]},
            ).one()
            self.assertEqual((project.team_id, project.lead_id), (None, None))
            self.assertEqual(
                conn.scalar(
                    text("select count(*) from members where id = :m"),
                    {"m": seeded["atom_member"]},
                ),
                0,
            )
            self.assertEqual(conn.scalar(text("select version_num from alembic_version")), "0017")

    def test_refuses_when_a_workspace_would_lose_its_only_owner(self):
        cfg, engine = self.fresh()
        with engine.begin() as conn:
            ws = conn.execute(
                text("insert into workspaces (name, slug) values ('W', 'w') returning id")
            ).scalar_one()
            user = conn.execute(
                text(
                    "insert into users (email, name, deleted_at) "
                    "values ('o@example.com', 'O', now()) returning id"
                )
            ).scalar_one()
            conn.execute(
                text(
                    "insert into members (workspace_id, user_id, type, role) "
                    "values (:w, :u, 'HUMAN', 'owner')"
                ),
                {"w": ws, "u": user},
            )
        with self.assertRaisesRegex(RuntimeError, "no active human owner"):
            command.upgrade(cfg, "head")
        with engine.connect() as conn:
            self.assertEqual(conn.scalar(text("select version_num from alembic_version")), "0011")
            self.assertEqual(conn.scalar(text("select count(*) from members")), 1)
