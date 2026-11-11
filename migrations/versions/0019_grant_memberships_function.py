"""Let the application role run memberships_for_user.

Migration 0005 granted this only when DATABASE_URL named a different user than the one running
the migration. In production the migration job's DATABASE_URL is the admin login too, so the
grant was skipped and listing a person's workspaces failed with "permission denied for function
memberships_for_user". APP_DB_ROLE names the application's role directly (a plain setting, no
password). Granting again is harmless when the role already has it. Nothing is revoked on the way down.

Revision ID: 0019
Revises: 0018
"""

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import make_url

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

FUNCTION = "public.memberships_for_user(uuid)"


def app_role(bind) -> str | None:
    """The role the API connects as: APP_DB_ROLE, else the user in DATABASE_URL, unless it is the
    role running the migration."""
    role = os.environ.get("APP_DB_ROLE", "").strip()
    if not role:
        url = os.environ.get("DATABASE_URL", "").strip()
        role = make_url(url).username if url else None
    current = bind.execute(sa.text("select current_user")).scalar()
    return role if role and role != current else None


def upgrade() -> None:
    bind = op.get_bind()
    role = app_role(bind)
    if role:
        quoted = bind.dialect.identifier_preparer.quote(role)
        op.execute(f"grant execute on function {FUNCTION} to {quoted}")


def downgrade() -> None:
    pass  # the grant stays: the API needs it
