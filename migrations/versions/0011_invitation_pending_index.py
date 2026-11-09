"""One live invitation per email in a workspace.

Stops, changing nothing, if a workspace has two pending invitations for one email: revoke all but
one and run again.

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    clashes = bind.execute(
        sa.text(
            "select workspace_id, lower(email) from invitations where status = 'pending' "
            "group by workspace_id, lower(email) having count(*) > 1"
        )
    ).all()
    if clashes:
        raise RuntimeError(
            "workspaces with two pending invitations for one email; revoke all but one and run "
            f"again: {[(str(row[0]), row[1]) for row in clashes]}"
        )
    op.create_index(
        "uq_invitations_pending_email",
        "invitations",
        ["workspace_id", sa.text("lower(email)")],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("uq_invitations_pending_email", table_name="invitations")
