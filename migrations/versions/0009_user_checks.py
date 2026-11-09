"""Database rules for users: the name must not be blank, the email must look like an address.

Stops, changing nothing, if a user breaks a rule: fix those users by hand and run again.

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

NAME_RULE = r"name ~ '\S'"
EMAIL_RULE = r"email ~ '^[^\s]+@[^\s@]+\.[^\s@]+$'"


def upgrade() -> None:
    bind = op.get_bind()
    broken = bind.execute(
        sa.text(f"select id from users where not ({NAME_RULE}) or not ({EMAIL_RULE}) order by id")
    ).all()
    if broken:
        raise RuntimeError(
            "users with a blank name or a malformed email; fix them and run again: "
            f"{[str(row[0]) for row in broken]}"
        )
    op.execute(f"alter table users add constraint ck_users_name_not_blank check ({NAME_RULE})")
    op.execute(f"alter table users add constraint ck_users_email_shape check ({EMAIL_RULE})")


def downgrade() -> None:
    op.execute("alter table users drop constraint if exists ck_users_email_shape")
    op.execute("alter table users drop constraint if exists ck_users_name_not_blank")
