"""Sprint lifecycle: one active sprint per project, and when a sprint started and completed.

Stops, changing nothing, if a project already has two active sprints: complete or fix one by hand.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    clashes = bind.execute(
        sa.text(
            "select project_id from sprints where status = 'active' "
            "group by project_id having count(*) > 1"
        )
    ).all()
    if clashes:
        raise RuntimeError(
            "projects with more than one active sprint; complete all but one and run again: "
            f"{[str(row[0]) for row in clashes]}"
        )
    op.execute("alter table sprints add column started_at timestamptz")
    op.execute("alter table sprints add column completed_at timestamptz")
    op.execute(
        "create unique index uq_sprints_one_active on sprints (project_id) where status = 'active'"
    )


def downgrade() -> None:
    op.execute("drop index if exists uq_sprints_one_active")
    op.execute("alter table sprints drop column if exists completed_at")
    op.execute("alter table sprints drop column if exists started_at")
