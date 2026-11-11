"""AI runs: paused agent runs and their human review decision (service key only).

Fresh installs get this table from the model (app/db/install.py grants every table). This
migration creates it on an existing database, adds the updated_at trigger and the tenant row-level
security the other tables get, and grants it to the role the API connects as, so migrated and
fresh databases match.

Revision ID: 0018
Revises: 0017
"""

import os
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

SCHEMA_0012 = Path(__file__).parent / "0012_hard_delete.sql"
TABLES = ("ai_runs",)

# The same policy app/db/sql/20_security.sql gives every workspace_id table (frozen here).
RLS = """
alter table ai_runs enable row level security;
alter table ai_runs force row level security;
drop policy if exists tenant_isolation on ai_runs;
create policy tenant_isolation on ai_runs
    using ((current_setting('app.service', true) = 'on'
            or workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid))
    with check ((current_setting('app.service', true) = 'on'
            or workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid));
"""


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
        "ai_runs",
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("agent_member_id", sa.UUID(), nullable=False),
        sa.Column("requester_member_id", sa.UUID(), nullable=False),
        sa.Column("stage", sa.String(), nullable=False),
        sa.Column("state", postgresql.JSONB(), nullable=False),
        sa.Column("review_status", sa.String(), server_default="pending", nullable=False),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("stage in ('plan','review','resuming')", name=op.f("ck_ai_runs_stage")),
        sa.CheckConstraint(
            "review_status in ('pending','approved','rejected')",
            name=op.f("ck_ai_runs_review_status"),
        ),
        sa.PrimaryKeyConstraint("task_id", name=op.f("pk_ai_runs")),
    )
    op.create_index("ix_ai_runs_workspace_id", "ai_runs", ["workspace_id"])
    op.execute(
        "create trigger ai_runs_set_updated_at before update on ai_runs "
        "for each row execute function set_updated_at()"
    )
    # The driver runs a file of several statements only as a plain simple query. The atom
    # policies are created for every workspace table, so the frozen atom SQL of 0012 (idempotent)
    # adds them to the new one as well.
    cursor = op.get_bind().connection.driver_connection.cursor()
    cursor.execute(RLS)
    cursor.execute(SCHEMA_0012.read_text())

    bind = op.get_bind()
    role = app_role(bind)
    if role:
        quoted = bind.dialect.identifier_preparer.quote(role)
        for table in TABLES:
            op.execute(f"grant select, insert, update, delete on {table} to {quoted}")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
