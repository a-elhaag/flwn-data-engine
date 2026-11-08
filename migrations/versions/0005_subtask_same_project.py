"""A sub-task must be in the same project as its parent task.

The parent key becomes (workspace_id, project_id, parent_task_id). Parents that point into another
project are cleared first, or the new key could not be created.

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

OLD = "fk_tasks_workspace_id_parent_task_id_tasks"
NEW = "fk_tasks_workspace_id_project_id_parent_task_id_tasks"


def upgrade() -> None:
    op.execute(
        """
        update tasks t set parent_task_id = null
        where parent_task_id is not null
          and not exists (select 1 from tasks p
                          where p.workspace_id = t.workspace_id and p.id = t.parent_task_id
                            and p.project_id = t.project_id)
        """
    )
    op.execute(f"alter table tasks drop constraint {OLD}")
    op.execute(
        f"alter table tasks add constraint {NEW} "
        "foreign key (workspace_id, project_id, parent_task_id) "
        "references tasks (workspace_id, project_id, id) on delete set null (parent_task_id)"
    )


def downgrade() -> None:
    op.execute(f"alter table tasks drop constraint {NEW}")
    op.execute(
        f"alter table tasks add constraint {OLD} "
        "foreign key (workspace_id, parent_task_id) "
        "references tasks (workspace_id, id) on delete set null (parent_task_id)"
    )
