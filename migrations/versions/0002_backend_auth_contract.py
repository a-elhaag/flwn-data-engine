"""Backend auth contract: canonical emails, cross-workspace memberships, last-owner backstop.

1. Rewrites every users.email to its canonical form (app/identity/email.py, identical to the
   backend's auth.CanonicalEmail). Stops, changing nothing, if an address cannot be
   canonicalized or two accounts would end up with the same address: merge or fix those by hand.
2. Adds memberships_for_user(uuid) (SECURITY DEFINER, not executable by PUBLIC), the deferred
   last-owner constraint triggers on members and the AI-never-owner trigger
   (0002_backend_auth_contract.sql, a frozen copy of app/db/sql/60_identity.sql).

Revision ID: 0002
Revises: 0001
"""

from collections import defaultdict
from pathlib import Path

import sqlalchemy as sa
from alembic import op

from app.domain.identity.email import InvalidEmail, canonical_email

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

SCHEMA = Path(__file__).with_suffix(".sql")


def canonicalize_emails(bind) -> int:
    """Rewrite users.email in canonical form; returns how many rows changed."""
    rows = bind.execute(sa.text("select id, email from users order by id")).all()
    invalid, by_address, changes = [], defaultdict(list), []
    for user_id, email in rows:
        try:
            canonical = canonical_email(email)
        except InvalidEmail:
            invalid.append(str(user_id))
            continue
        by_address[canonical.lower()].append(str(user_id))  # uq_users_email is on lower(email)
        if canonical != email:
            changes.append((user_id, canonical))
    collisions = [ids for ids in by_address.values() if len(ids) > 1]
    if invalid or collisions:
        raise RuntimeError(
            "cannot canonicalize users.email; fix these accounts and run the migration again. "
            f"invalid addresses (user ids): {invalid}; "
            f"accounts sharing one canonical address (user ids): {collisions}"
        )
    for user_id, canonical in changes:
        bind.execute(
            sa.text("update users set email = :email where id = :id"),
            {"email": canonical, "id": user_id},
        )
    return len(changes)


def upgrade() -> None:
    bind = op.get_bind()
    canonicalize_emails(bind)
    # The driver runs a file of several statements only as a plain simple query.
    bind.connection.driver_connection.cursor().execute(SCHEMA.read_text())


def downgrade() -> None:
    # Canonical emails stay canonical: the original spellings are not kept.
    op.execute("drop trigger if exists members_owner_is_human on members")
    op.execute("drop trigger if exists members_keep_an_owner_delete on members")
    op.execute("drop trigger if exists members_keep_an_owner_update on members")
    op.execute("drop function if exists members_owner_is_human()")
    op.execute("drop function if exists members_keep_an_owner()")
    op.execute("drop function if exists memberships_for_user(uuid)")
