"""Per-member row-level security on the private tables (see app/db/sql/60_member_privacy.sql).

Revision ID: 0003
Revises: 0002
"""

from pathlib import Path

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

POLICY = Path(__file__).resolve().parents[2] / "app" / "db" / "sql" / "60_member_privacy.sql"


def upgrade() -> None:
    # The driver runs a file of several statements only as a plain simple query.
    op.get_bind().connection.driver_connection.cursor().execute(POLICY.read_text())


def downgrade() -> None:
    for table in ("notifications", "favorites", "subscriptions", "llm_usage", "memories"):
        op.execute(f"drop policy if exists member_private on {table}")
