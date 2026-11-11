"""Let the application role run memberships_for_user.

Migration 0005 granted this only when DATABASE_URL named a different user than the one running
the migration. A database set up another way (production) ended up without the grant, so listing
a person's workspaces failed with "permission denied for function memberships_for_user".
Granting again is harmless when the role already has it. Nothing is revoked on the way down.

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
    """The role the API connects as (DATABASE_URL), unless it is the role running the migration."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return None
    role = make_url(url).username
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
