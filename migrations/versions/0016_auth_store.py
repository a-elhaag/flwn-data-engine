"""Auth store: password hashes, refresh sessions, login codes and OAuth links (hashes only).

Fresh installs get these tables from the models (app/db/install.py grants every table). This
migration creates them on an existing database and grants them to the role the API connects as,
the same way 0005 does, so migrated and fresh databases match.

Revision ID: 0016
Revises: 0015
"""

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import make_url

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

TABLES = ("user_credentials", "auth_sessions", "login_codes", "oauth_identities")


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
        "user_credentials",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_credentials_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_credentials")),
    )
    op.execute(
        "create trigger user_credentials_set_updated_at before update on user_credentials "
        "for each row execute function set_updated_at()"
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("refresh_hash", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_auth_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_sessions")),
        sa.UniqueConstraint("refresh_hash", name=op.f("uq_auth_sessions_refresh_hash")),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_table(
        "login_codes",
        sa.Column("code_hash", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("code_hash", name=op.f("pk_login_codes")),
    )
    op.create_table(
        "oauth_identities",
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "provider in ('google','github')", name=op.f("ck_oauth_identities_provider_known")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_oauth_identities_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("provider", "subject", name=op.f("pk_oauth_identities")),
    )

    bind = op.get_bind()
    role = app_role(bind)
    if role:
        quoted = bind.dialect.identifier_preparer.quote(role)
        for table in TABLES:
            op.execute(f"grant select, insert, update, delete on {table} to {quoted}")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
