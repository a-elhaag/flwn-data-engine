"""Private teams are out of the MVP: drop teams.visibility, every team is visible to the workspace.

Stops, changing nothing, if any team is still private: dropping the column would silently make
it visible to the whole workspace. Make those teams 'workspace' (or delete them) and run again.
The atom owner check is replaced first (0010_team_visibility.sql, a frozen copy of
app/db/sql/40_atom_access.sql) so nothing still reads the column.

Downgrade puts the column back with every team 'workspace', which is what every team was
treated as after this migration.

Revision ID: 0010
Revises: 0009
"""

from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

SCHEMA = Path(__file__).with_suffix(".sql")


def upgrade() -> None:
    private = (
        op.get_bind()
        .execute(sa.text("select id from teams where visibility = 'private' order by id"))
        .scalars()
        .all()
    )
    if private:
        raise RuntimeError(
            "private teams still exist, and dropping visibility would make them visible to the "
            f"whole workspace; set them to 'workspace' or delete them, then run again. team ids: {[str(i) for i in private]}"
        )
    # The driver runs a file of several statements only as a plain simple query.
    op.get_bind().connection.driver_connection.cursor().execute(SCHEMA.read_text())
    op.execute("alter table teams drop constraint if exists ck_teams_visibility")
    op.execute("alter table teams drop column if exists visibility")


def downgrade() -> None:
    op.execute("alter table teams add column visibility varchar not null default 'workspace'")
    op.execute(
        "alter table teams add constraint ck_teams_visibility "
        "check (visibility in ('workspace', 'private'))"
    )
