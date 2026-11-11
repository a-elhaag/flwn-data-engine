"""Rate-limit counters: one row per counted attempt, keyed by a hash (no raw email or IP).

Fresh installs get this table from the model (app/db/install.py grants every table). This
migration creates it on an existing database and grants it to the role the API connects as,
the same way 0016 does, so migrated and fresh databases match.

Revision ID: 0017
Revises: 0016
"""

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import make_url

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

TABLES = ("rate_hits",)


def app_role(bind) -> str | None:
    """The role the API connects as (DATABASE_URL), unless it is the role running the migration."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return None
    role = make_url(url).username
    current = bind.execute(sa.text("select current_user")).scalar()
    return role if role and role != current else None


def upgrade() -> None:
    op.create_table(
        "rate_hits",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("bucket", sa.String(length=40), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "hit_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rate_hits")),
    )
    op.create_index(
        "ix_rate_hits_bucket_key_hash_hit_at",
        "rate_hits",
        ["bucket", "key_hash", "hit_at"],
    )

    bind = op.get_bind()
    role = app_role(bind)
    if role:
        quoted = bind.dialect.identifier_preparer.quote(role)
        for table in TABLES:
            op.execute(f"grant select, insert, update, delete on {table} to {quoted}")
        op.execute(f"grant usage, select on sequence rate_hits_id_seq to {quoted}")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
