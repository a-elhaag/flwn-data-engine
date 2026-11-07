"""The canonical form of an account's email address.

This must return exactly what the backend's `auth.CanonicalEmail` returns
(flwnBackend/internal/auth/email.go, docs/AUTH.md §12): the backend owns the definition and the
engine stores the string it produces, so lookups and the unique index compare the same thing.

1. trim surrounding whitespace
2. reject control characters and whitespace inside, length > 254 bytes, no "@", empty local part
   or domain
3. split at the last "@" (an "@" in the local part is allowed only inside a quoted local part)
4. domain: IDNA (UTS #46, non-transitional, STD3 rules) to ASCII punycode, lowercase; it must
   contain a dot and must not start or end with one
5. local part: Unicode NFC, then lowercase one character at a time (Go's simple case mapping)
6. no dot or +tag stripping: those would merge distinct mailboxes

Lengths are counted in UTF-8 bytes, as Go's len() does, and so is Go's hyphen rule. Checked
against the Go code on 40,000 random addresses: whenever both accept, the strings are identical,
and nothing Go rejects is accepted here. Known refusals where Go accepts: symbols UTS #46 allows
but IDNA 2008 does not (emoji domains), and malformed "xn--" labels Go lets through.
"""

import unicodedata

import idna


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


def _check_label(label: str) -> None:
    """idna.check_label without its character-based hyphen rule (see _hyphens_ok)."""
    idna.core.check_nfc(label)
    idna.core.check_initial_combiner(label)
    classes = idna.idnadata.codepoint_classes
    for pos, char in enumerate(label):
        code = ord(char)
        if idna.intranges.intranges_contain(code, classes["PVALID"]):
            continue
        if idna.intranges.intranges_contain(code, classes["CONTEXTJ"]):
            if idna.core.valid_contextj(label, pos):
                continue
        elif idna.intranges.intranges_contain(code, classes["CONTEXTO"]):
            if idna.core.valid_contexto(label, pos):
                continue
        raise InvalidEmail()
    idna.core.check_bidi(label)


def _domain_to_ascii(domain: str) -> str:
    """UTS #46 lookup mapping (non-transitional, STD3), then punycode, as Go's idna profile."""
    try:
        mapped = idna.uts46_remap(domain, std3_rules=True, transitional=False)
        labels = []
        for label in mapped.split("."):
            if not label:  # Go keeps empty labels unless DNS lengths are verified
                labels.append(label)
                continue
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


def canonical_or_none(raw: str) -> str | None:
    """For lookups: an address that cannot be canonical cannot belong to anyone."""
    try:
        return canonical_email(raw)
    except InvalidEmail:
        return None
