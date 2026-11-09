"""Blob purge queue: a workspace delete records its file blobs for a reaper to delete from storage.

A workspace delete removes rows in the database but cannot delete the bytes inside the request.
It writes one blob_purges row per file in the same transaction, and a reaper deletes the blobs
later. The table carries workspace_id but no foreign key, because the workspace is gone by then.
It gets the same row-level security as every table with a workspace_id (db/sql/20_security.sql).

Revision ID: 0013
Revises: 0012
"""

from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

SCHEMA_0012 = Path(__file__).parent / "0012_hard_delete.sql"

# The same policy app/db/sql/20_security.sql gives every workspace_id table (frozen here).
RLS = """
alter table blob_purges enable row level security;
alter table blob_purges force row level security;
drop policy if exists tenant_isolation on blob_purges;
create policy tenant_isolation on blob_purges
    using ((current_setting('app.service', true) = 'on'
            or workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid))
    with check ((current_setting('app.service', true) = 'on'
            or workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid));
"""


def upgrade() -> None:
    op.create_table(
        "blob_purges",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("container", sa.String(), nullable=False),
        sa.Column("blob_path", sa.String(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_blob_purges")),
    )
    # The driver runs a file of several statements only as a plain simple query. The atom
    # policies are created for every workspace table, so the frozen atom SQL of 0012 (idempotent)
    # adds them to the new one as well.
    cursor = op.get_bind().connection.driver_connection.cursor()
    cursor.execute(RLS)
    cursor.execute(SCHEMA_0012.read_text())


def downgrade() -> None:
    op.drop_table("blob_purges")
