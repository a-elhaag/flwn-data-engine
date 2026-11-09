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

Lengths are counted in UTF-8 bytes, as Go's len() does, and so is Go's hyphen rule. Only idna's
public API is used (uts46_remap, check_initial_combiner, valid_contextj, check_bidi); idna is
pinned below 4 in requirements.txt. tests/identity/test_email_canonical.py holds vectors produced
by the Go code, so a change on either side fails a test.
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


def canonical_or_none(raw: str) -> str | None:
    """For lookups: an address that cannot be canonical cannot belong to anyone."""
    try:
        return canonical_email(raw)
    except InvalidEmail:
        return None
