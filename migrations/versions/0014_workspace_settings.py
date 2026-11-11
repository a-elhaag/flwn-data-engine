"""Workspace settings split into two sections: `engine` (keys the engine reads) and `product`.

Issue #40. Until now workspaces.settings was one flat object. Each existing object is wrapped
under `product`, except that the keys the engine owns move to `engine`: `recording_retention_days`
and the dev sign-in marker `dev_session`. An object that already has an `engine` or `product`
section is left alone, and an empty object stays empty.

Downgrade merges the two sections back into one flat object.

Revision ID: 0014
Revises: 0013
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

ENGINE_KEYS = "('dev_session', 'recording_retention_days')"

# workspaces is under row-level security with FORCE, so the migration sets app.service to see
# and change every workspace (the same switch the trusted service code uses).
UPGRADE = f"""
select set_config('app.service', 'on', true);
update workspaces
set settings = jsonb_build_object('product', settings - 'dev_session' - 'recording_retention_days')
    || coalesce(
        (select jsonb_build_object('engine', jsonb_object_agg(e.key, e.value))
         from jsonb_each(workspaces.settings) as e(key, value)
         where e.key in {ENGINE_KEYS}
         having count(*) > 0),
        '{{}}'::jsonb)
where settings <> '{{}}'::jsonb
  and not (settings ? 'engine')
  and not (settings ? 'product');
"""

DOWNGRADE = """
select set_config('app.service', 'on', true);
update workspaces
set settings = coalesce(settings -> 'product', '{}'::jsonb) || coalesce(settings -> 'engine', '{}'::jsonb)
where settings ? 'engine' or settings ? 'product';
"""


def upgrade() -> None:
    op.get_bind().connection.driver_connection.cursor().execute(UPGRADE)


def downgrade() -> None:
    op.get_bind().connection.driver_connection.cursor().execute(DOWNGRADE)
