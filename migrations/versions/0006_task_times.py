"""The database sets tasks.started_at and completed_at from the task's status.

Existing tasks keep the values they have (all null today): the real times cannot be rebuilt.

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

# A frozen copy of app/db/sql/12_task_times.sql as of this migration.
SQL = """
-- tasks.started_at and completed_at are kept by the database, so every writer is covered.
-- started_at: the first time the status becomes in_progress or in_review; it is never cleared.
-- completed_at: set while the status is done or canceled; a task moved back out of those clears it.
-- Values sent by a writer are ignored. Idempotent.
create or replace function set_task_times() returns trigger
language plpgsql as $$
begin
    new.started_at := case when tg_op = 'UPDATE' then old.started_at end;
    new.completed_at := case when tg_op = 'UPDATE' then old.completed_at end;
    if new.status in ('in_progress', 'in_review') then
        new.started_at := coalesce(new.started_at, now());
    end if;
    if new.status in ('done', 'canceled') then
        new.completed_at := coalesce(new.completed_at, now());
    else
        new.completed_at := null;
    end if;
    return new;
end $$;

drop trigger if exists tasks_set_times on tasks;
create trigger tasks_set_times before insert or update on tasks
    for each row execute function set_task_times();
"""


def upgrade() -> None:
    op.get_bind().connection.driver_connection.cursor().execute(SQL)


def downgrade() -> None:
    op.execute("drop trigger if exists tasks_set_times on tasks")
    op.execute("drop function if exists set_task_times()")
