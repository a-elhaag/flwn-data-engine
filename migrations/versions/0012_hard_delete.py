"""Hard delete everywhere: drop deleted_at, purge the soft-deleted rows, set the delete actions.

Issue #26. Every table that had a deleted_at column loses it. A row that was soft-deleted is
removed for good, because a surviving row would silently come back to life once the column is
gone. The purge runs first, in this order, inside the migration transaction. Rows that go by a
cascade are not counted separately.

  1. workspaces with deleted_at set (everything inside them cascades)
  2. members of soft-deleted users, then the users (memberships are RESTRICT on users)
  3. members with deleted_at set (their team seats cascade, their work is set to null)
  4. teams, projects (work items, tasks and comments cascade), tasks, work items
  5. folders, files, collections, docs (sub-docs cascade), messages, comments
  6. atoms (their versions go with them), then the member identities they used
  7. skills and catalog skills (atoms that had them detach them)

The counts removed are logged while the migration runs (logger alembic.runtime.migration),
because they depend on the database being migrated. The migration refuses, changing nothing, in
two cases:

  - a live workspace would lose its last active human owner because that owner was
    soft-deleted; give it another owner and run again;
  - a soft-deleted row is still referenced by live data through a restrict foreign key (for
    example an atom owned by a soft-deleted member); transfer or delete that data and run again.

Foreign key actions changed here (NO ACTION is restrict; everything else is as in 0011):

  atom_skills.skill               -> skills    cascade (a deleted skill detaches)
  atom_skills.catalog skill       -> catalogs  cascade (a deleted catalog skill detaches)
Atoms: the atom's member is its identity; the service deletes the atom, then its member.

Atom approval records are durable (a trigger refuses their deletion), so deleting an atom that
has any is refused with atom_in_use; kill the atom instead.

The partial unique index on folder names drops its WHERE clause. The atom, approval and identity
functions and triggers are replaced by the frozen copy in 0012_hard_delete.sql. Downgrade is not
supported: purged rows cannot come back.

Revision ID: 0012
Revises: 0011
"""

import logging
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

SCHEMA = Path(__file__).with_suffix(".sql")
log = logging.getLogger("alembic.runtime.migration")

# Order matters: parents of restrict references first, so a refusal names the real blocker.
PURGE = (
    ("workspaces", "delete from workspaces where deleted_at is not null"),
    (
        "memberships of soft-deleted users",
        "delete from members where user_id in (select id from users where deleted_at is not null)",
    ),
    ("users", "delete from users where deleted_at is not null"),
    ("members", "delete from members where deleted_at is not null"),
    ("teams", "delete from teams where deleted_at is not null"),
    ("projects", "delete from projects where deleted_at is not null"),
    ("tasks", "delete from tasks where deleted_at is not null"),
    ("work items", "delete from work_items where deleted_at is not null"),
    ("folders", "delete from folders where deleted_at is not null"),
    ("files", "delete from files where deleted_at is not null"),
    ("collections", "delete from collections where deleted_at is not null"),
    ("docs", "delete from docs where deleted_at is not null"),
    ("messages", "delete from messages where deleted_at is not null"),
    ("comments", "delete from comments where deleted_at is not null"),
    ("atoms", "delete from atoms where deleted_at is not null"),
    ("skills", "delete from skills where deleted_at is not null"),
    ("catalog skills", "delete from catalog_skills where deleted_at is not null"),
)

TABLES = (
    "users",
    "workspaces",
    "members",
    "teams",
    "projects",
    "work_items",
    "tasks",
    "folders",
    "files",
    "atoms",
    "skills",
    "catalog_skills",
    "collections",
    "docs",
    "messages",
    "comments",
)

# (name, table, columns, referenced table, referenced columns, ondelete)
FOREIGN_KEYS = (
    (
        "fk_atom_skills_workspace_id_skill_id_skill_version_skills",
        "atom_skills",
        ["workspace_id", "skill_id", "skill_version"],
        "skills",
        ["workspace_id", "id", "version"],
        "CASCADE",
    ),
    (
        "fk_atom_skills_catalog_skill_id_skill_version_catalog_skills",
        "atom_skills",
        ["catalog_skill_id", "skill_version"],
        "catalog_skills",
        ["id", "version"],
        "CASCADE",
    ),
)


def _purge(bind, label, statement, params=None) -> None:
    try:
        removed = bind.execute(sa.text(statement), params or {}).rowcount
    except sa.exc.IntegrityError as exc:
        raise RuntimeError(
            f"0012: cannot purge soft-deleted {label}: live data still references them. "
            f"Transfer or delete that data, then run again. Database says: {exc.orig}"
        ) from exc
    log.info("0012 hard delete: removed %s %s", removed, label)


def upgrade() -> None:
    bind = op.get_bind()

    # The delete actions must be right before any row is removed.
    for name, table, columns, target, target_columns, ondelete in FOREIGN_KEYS:
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(name, table, target, columns, target_columns, ondelete=ondelete)

    identities = (
        bind.execute(sa.text("select member_id from atoms where deleted_at is not null"))
        .scalars()
        .all()
    )
    for label, statement in PURGE:
        _purge(bind, label, statement)
        if label == "atoms" and identities:
            _purge(
                bind,
                "atom identities of soft-deleted atoms",
                "delete from members where id in (select unnest(cast(:ids as uuid[])))",
                {"ids": identities},
            )

    # The last-owner trigger on members is deferred. Run it now, so a workspace whose only owner
    # was purged is refused here, with nothing changed, rather than at commit or by ALTER TABLE.
    try:
        bind.execute(sa.text("set constraints all immediate"))
    except sa.exc.IntegrityError as exc:
        raise RuntimeError(
            "0012: removing soft-deleted members or users would leave a live workspace with no "
            "active human owner. Give that workspace another owner and run again. "
            f"Database says: {exc.orig}"
        ) from exc
    bind.execute(sa.text("set constraints all deferred"))

    # These depend on members.deleted_at; the frozen SQL below recreates them without it.
    op.execute("drop trigger if exists members_keep_an_owner_update on members")
    op.execute("drop trigger if exists members_keep_an_owner_delete on members")
    op.execute("drop index if exists uq_folders_name")
    op.execute("alter table atoms drop constraint if exists ck_atoms_active_version_required")
    for table in TABLES:
        op.drop_column(table, "deleted_at")
    op.execute(
        "create unique index uq_folders_name on folders (workspace_id, "
        "coalesce(parent_folder_id, '00000000-0000-0000-0000-000000000000'::uuid), lower(name))"
    )
    op.create_check_constraint(
        "active_version_required",
        "atoms",
        "status <> 'active' or active_version_id is not null",
    )
    # The driver runs a file of several statements only as a plain simple query.
    bind.connection.driver_connection.cursor().execute(SCHEMA.read_text())


def downgrade() -> None:
    raise RuntimeError(
        "0012 cannot be reversed: the soft-deleted rows were purged. Restore from a backup."
    )
