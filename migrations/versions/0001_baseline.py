"""Baseline: the whole schema as of the dev + feature merge.

The schema is a frozen pg_dump (0001_baseline.sql), not a call into the live models, so this
migration builds the same database forever, whatever the models become. It includes the tables,
indexes, functions, triggers and the row-level security policies that autogenerate cannot see.
Later migrations are written on top of it.

Revision ID: 0001
Revises:
"""

from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = Path(__file__).with_suffix(".sql")


def upgrade() -> None:
    # The driver runs a file of several statements only as a plain simple query.
    raw = op.get_bind().connection.driver_connection.cursor()
    raw.execute("set local check_function_bodies = false")
    raw.execute(SCHEMA.read_text())


def downgrade() -> None:
    raise NotImplementedError("the baseline cannot be undone; drop the database instead")
