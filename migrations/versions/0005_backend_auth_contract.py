"""Backend auth contract: canonical emails, cross-workspace memberships, last-owner backstop.

1. Rewrites every users.email to its canonical form (identical to the backend's
   auth.CanonicalEmail). Stops, changing nothing, if an address cannot be canonicalized or two
   accounts would end up with the same address: merge or fix those by hand.
2. Adds memberships_for_user(uuid) (SECURITY DEFINER, not executable by PUBLIC), the deferred
   last-owner constraint triggers on members and the AI-never-owner trigger
   (0005_backend_auth_contract.sql, a frozen copy of app/db/sql/70_identity.sql).
3. Grants execute on memberships_for_user only to the application role: the user in
   DATABASE_URL (the role the API connects as). app/db/install.py does the same on a fresh install.

Self-contained on purpose: the canonical-email code below is a frozen copy of
app/domain/identity/email.py as of this revision, so later changes or moves of app code cannot
change what this migration does.

Revision ID: 0005
Revises: 0004
"""

import os
import unicodedata
from collections import defaultdict
from pathlib import Path

import idna
import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import make_url

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

SCHEMA = Path(__file__).with_suffix(".sql")
FUNCTION = "public.memberships_for_user(uuid)"


# ---- frozen copy of app/domain/identity/email.py (revision 0005) ----------------------------


class InvalidEmail(ValueError):
    def __init__(self, message: str = "invalid email address"):
        super().__init__(message)


# Go's unicode.IsSpace (and so strings.TrimSpace): Latin-1 spaces plus the White_Space property.
_SPACE = frozenset(
    chr(code)
    for code in (0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x20, 0x85, 0xA0, 0x1680, 0x2028, 0x2029)
    + (0x202F, 0x205F, 0x3000)
    + tuple(range(0x2000, 0x200B))
)


def _control(char: str) -> bool:
    """Go's unicode.IsControl: the C0 and C1 control codes."""
    code = ord(char)
    return code < 0x20 or 0x7F <= code <= 0x9F


def _lower(text: str) -> str:
    """Go's strings.ToLower: per-character simple lowercase (Python's str.lower is contextual)."""
    out = []
    for char in text:
        lowered = char.lower()
        out.append(lowered if len(lowered) == 1 else lowered[0])  # U+0130 lowers to "i"
    return "".join(out)


def _hyphens_ok(label: str) -> bool:
    """Go's CheckHyphens, which tests the 3rd and 4th BYTES of the UTF-8 label, not characters."""
    raw = label.encode()
    return not (raw[2:4] == b"--" or raw.startswith(b"-") or raw.endswith(b"-"))


_JOINERS = frozenset("‌‍")  # ZWNJ, ZWJ: the CONTEXTJ code points


def _check_label(label: str) -> None:
    """The label checks Go's lookup profile makes after UTS #46 mapping, using only idna's public
    API: no leading combining mark, joiners only in a valid context, the bidi rule.

    Go does not apply IDNA 2008's per-code-point classes (PVALID/CONTEXTO), so neither do we:
    an address the backend accepts must never be refused here.
    """
    idna.check_initial_combiner(label)
    for pos, char in enumerate(label):
        if char in _JOINERS and not idna.valid_contextj(label, pos):
            raise InvalidEmail()
    idna.check_bidi(label)


_FULL_STOPS = str.maketrans({"。": ".", "．": ".", "｡": "."})


def _labels_ok(domain: str) -> bool:
    """The backend's labelsOK, on the raw domain before mapping: no empty label, no bare "xn--"
    label, no "xn--" label holding non-ASCII characters."""
    for label in domain.translate(_FULL_STOPS).split("."):
        if not label:
            return False
        lowered = _lower(label)
        if lowered.startswith("xn--") and (len(lowered) == 4 or not label.isascii()):
            return False
    return True


def _domain_to_ascii(domain: str) -> str:
    """UTS #46 lookup mapping (non-transitional, STD3), then punycode, as Go's idna profile."""
    if not _labels_ok(domain):
        raise InvalidEmail()
    try:
        mapped = idna.uts46_remap(domain, std3_rules=True, transitional=False)
        labels = []
        for label in mapped.split("."):
            if not label:  # "a..b", a leading or trailing dot: refused, as the backend does
                raise InvalidEmail()
            if label.startswith("xn--"):
                unicode_label = label[4:].encode("ascii").decode("punycode")
                # an ACE label must decode to a mapped, non-ASCII label that encodes back to it
                if (
                    unicode_label.isascii()
                    or "xn--" + unicode_label.encode("punycode").decode("ascii") != label
                    or idna.uts46_remap(unicode_label, std3_rules=True, transitional=False)
                    != unicode_label
                ):
                    raise InvalidEmail()
                ascii_label = label
            elif label.isascii():
                unicode_label = ascii_label = label
            else:
                unicode_label = label
                ascii_label = "xn--" + label.encode("punycode").decode("ascii")
            if not _hyphens_ok(unicode_label):
                raise InvalidEmail()
            if not unicode_label.isascii():
                _check_label(unicode_label)
            labels.append(ascii_label)
    except (idna.IDNAError, UnicodeError, ValueError):
        raise InvalidEmail() from None
    return ".".join(labels)


def canonical_email(raw: str) -> str:
    """The canonical address, or InvalidEmail (a ValueError) when there is none."""
    s = raw.strip("".join(_SPACE))
    if not s or len(s.encode()) > 254:
        raise InvalidEmail()
    if any(_control(char) or char in _SPACE for char in s):
        raise InvalidEmail()
    at = s.rfind("@")
    if at <= 0 or at == len(s) - 1:
        raise InvalidEmail()
    local, domain = s[:at], s[at + 1 :]
    if "@" in local and not (local.startswith('"') and local.endswith('"')):
        raise InvalidEmail()
    ascii_domain = _domain_to_ascii(domain)
    if (
        not ascii_domain
        or "." not in ascii_domain
        or ascii_domain[0] == "."
        or ascii_domain[-1] == "."
    ):
        raise InvalidEmail()
    local = _lower(unicodedata.normalize("NFC", local))
    out = local + "@" + ascii_domain.lower()
    if len(out.encode()) > 254 or len(local.encode()) > 64:
        raise InvalidEmail()
    return out


# ---- migration ------------------------------------------------------------------------------


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


def app_role(bind) -> str | None:
    """The role the API connects as (DATABASE_URL), unless it is the role running the migration."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return None
    role = make_url(url).username
    current = bind.execute(sa.text("select current_user")).scalar()
    return role if role and role != current else None


def upgrade() -> None:
    bind = op.get_bind()
    canonicalize_emails(bind)
    # The driver runs a file of several statements only as a plain simple query.
    with bind.connection.driver_connection.cursor() as cursor:
        cursor.execute(SCHEMA.read_text())
    role = app_role(bind)
    if role:
        quoted = bind.dialect.identifier_preparer.quote(role)
        op.execute(f"grant execute on function {FUNCTION} to {quoted}")


def downgrade() -> None:
    # Canonical emails stay canonical: the original spellings are not kept.
    bind = op.get_bind()
    role = app_role(bind)
    if role:
        quoted = bind.dialect.identifier_preparer.quote(role)
        op.execute(f"revoke execute on function {FUNCTION} from {quoted}")
    op.execute("drop trigger if exists members_owner_is_human on members")
    op.execute("drop trigger if exists members_keep_an_owner_delete on members")
    op.execute("drop trigger if exists members_keep_an_owner_update on members")
    op.execute("drop function if exists members_owner_is_human()")
    op.execute("drop function if exists members_keep_an_owner()")
    op.execute("drop function if exists memberships_for_user(uuid)")
