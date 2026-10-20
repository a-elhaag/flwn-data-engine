"""A throwaway Postgres for tests.

Set TEST_DATABASE_URL to an admin URL (the tests create and drop their own database), or
`pip install pgserver` for a local one. Without either, the database tests skip.
"""

import atexit
import os
import tempfile
import unittest
import uuid

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.db.install import install

_state: dict = {}


def engine():
    """Engine on a fresh database with the schema installed. Created once per test run."""
    if "engine" in _state:
        return _state["engine"]
    admin_url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not admin_url:
        try:
            import pgserver
        except ImportError:
            raise unittest.SkipTest(
                "set TEST_DATABASE_URL or install pgserver to run database tests"
            ) from None
        _state["server"] = pgserver.get_server(tempfile.mkdtemp(prefix="flwn_pg_"))
        admin_url = _state["server"].get_uri().replace("postgresql://", "postgresql+psycopg://", 1)
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    name = f"flwn_test_{uuid.uuid4().hex[:8]}"
    with admin.connect() as conn:
        conn.execute(text(f'create database "{name}"'))
    url = make_url(admin_url).set(database=name).render_as_string(hide_password=False)
    install(url)
    _state.update(admin=admin, name=name, engine=create_engine(url, pool_size=10))
    atexit.register(_cleanup)
    return _state["engine"]


def _cleanup():
    _state["engine"].dispose()
    with _state["admin"].connect() as conn:
        conn.execute(text(f'drop database "{_state["name"]}" with (force)'))
    _state["admin"].dispose()
    if "server" in _state:
        _state["server"].cleanup()
