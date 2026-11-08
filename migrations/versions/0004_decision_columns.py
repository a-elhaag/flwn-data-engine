"""Decision Ledger columns missing from databases installed before the baseline was frozen.

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("alter table decision_conflicts add column if not exists proposal varchar")
    op.execute(
        "alter table decisions add column if not exists scope_paths jsonb not null default '[]'::jsonb"
    )


def downgrade() -> None:
    op.execute("alter table decisions drop column if exists scope_paths")
    op.execute("alter table decision_conflicts drop column if exists proposal")
