"""A team that owns projects cannot be deleted: projects.team_id is ON DELETE RESTRICT.

Issue #41. Migration 0012 set this foreign key to SET NULL, which turned a deleted team's
projects into workspace-wide ones without a warning. The team service now refuses the delete with
409 team_has_projects; the database refuses it too, so no other path can remove a team that
still owns projects.

Revision ID: 0015
Revises: 0014
"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

NAME = "fk_projects_workspace_id_team_id_teams"


def _team_fk(ondelete: str) -> None:
    op.drop_constraint(NAME, "projects", type_="foreignkey")
    op.create_foreign_key(
        NAME,
        "projects",
        "teams",
        ["workspace_id", "team_id"],
        ["workspace_id", "id"],
        ondelete=ondelete,
    )


def upgrade() -> None:
    _team_fk("RESTRICT")


def downgrade() -> None:
    _team_fk("SET NULL")
