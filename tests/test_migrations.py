"""The Alembic baseline must build exactly the schema that app.db.install builds."""

import os
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import db_support
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.db.base import Base

ROOT = Path(__file__).resolve().parent.parent

# Everything the models cannot express: these are what a hand-made migration usually misses.
CATALOGS = {
    "tables": "select tablename, rowsecurity from pg_tables join pg_class c on c.relname = tablename"
    " join pg_namespace n on n.oid = c.relnamespace and n.nspname = schemaname"
    " where schemaname = 'public'",
    "policies": "select tablename, policyname, cmd, qual, with_check from pg_policies",
    "triggers": "select tgrelid::regclass::text, tgname from pg_trigger where not tgisinternal",
    "functions": "select p.proname, pg_get_function_arguments(p.oid) from pg_proc p"
    " join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'public'"
    " and not exists (select 1 from pg_depend d where d.objid = p.oid and d.deptype = 'e')",
    # name, table and uniqueness only: a restored partial index predicate is printed differently
    "indexes": "select c.relname, i.indrelid::regclass::text, i.indisunique from pg_index i"
    " join pg_class c on c.oid = i.indexrelid where c.relnamespace = 'public'::regnamespace",
    # name and kind only: pg_get_constraintdef prints a CHECK differently after a dump and restore
    "constraints": "select conrelid::regclass::text, conname, contype"
    " from pg_constraint where connamespace = 'public'::regnamespace",
}


def snapshot(engine):
    with engine.connect() as conn:
        return {
            name: sorted(
                tuple(map(str, row))
                for row in conn.execute(text(query))
                if "alembic_version" not in str(row)  # Alembic's own bookkeeping table
            )
            for name, query in CATALOGS.items()
        }


class MigrationBaselineTest(unittest.TestCase):
    def test_baseline_matches_install_and_models(self):
        installed = db_support.engine()
        admin = db_support._state["admin"]
        name = f"flwn_mig_{uuid.uuid4().hex[:8]}"
        with admin.connect() as conn:
            conn.execute(text(f'create database "{name}"'))
        self.addCleanup(self._drop, admin, name)
        url = (
            make_url(installed.url.render_as_string(hide_password=False))
            .set(database=name)
            .render_as_string(hide_password=False)
        )
        with patch.dict(os.environ, {"DATABASE_ADMIN_URL": url, "DATABASE_URL": url}):
            cfg = Config(str(ROOT / "alembic.ini"))
            cfg.set_main_option("script_location", str(ROOT / "migrations"))
            command.upgrade(cfg, "head")

        migrated = create_engine(url)
        self.addCleanup(migrated.dispose)
        built, expected = snapshot(migrated), snapshot(installed)
        for catalog in CATALOGS:
            with self.subTest(catalog=catalog):
                self.assertEqual(built[catalog], expected[catalog])
        with migrated.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
        self.assertEqual(diff, [], "models and migrations disagree")

    @staticmethod
    def _drop(admin, name):
        with admin.connect() as conn:
            conn.execute(text(f'drop database "{name}" with (force)'))


if __name__ == "__main__":
    unittest.main()
