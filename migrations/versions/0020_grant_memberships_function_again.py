"""Grant memberships_for_user to the application role (again, with APP_DB_ROLE set).

Migration 0019 ran on production before the job knew the application's role name (its
DATABASE_URL is the admin login), so it granted nothing. This one runs with APP_DB_ROLE set on the
job. Granting twice is harmless, and nothing is revoked on the way down.

Revision ID: 0020
Revises: 0019
"""

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import make_url

revision = "0020"
down_revision = "0019"
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
