"""Canonical emails must match the backend's auth.CanonicalEmail exactly.

The first two tables are the backend's TestCanonicalEmail vectors
(flwnBackend/internal/auth/core_test.go); keep them in step with it.
"""

import unittest

import idna

from app.domain.identity.email import InvalidEmail, canonical_email, canonical_or_none

BACKEND_OK = {
    "  Alice@Example.COM ": "alice@example.com",
    "a.b+tag@gmail.com": "a.b+tag@gmail.com",  # no dot/+ stripping
    "user@Bücher.example": "user@xn--bcher-kva.example",
    "ÉLODIE@example.com": "élodie@example.com",
    "Élodie@example.com": "élodie@example.com",  # NFC
    '"weird@local"@example.com': '"weird@local"@example.com',
}
BACKEND_BAD = [
    "",
    "no-at",
    "@example.com",
    "a@",
    "a@localhost",
    "a b@example.com",
    "a@exa mple.com",
    "a\x00@example.com",
    "a" * 65 + "@example.com",
    "a@" + "b" * 250 + ".com",
    "a@.example.com",
    "a@example.com.",
]

# Further cases that follow from the same Go rules.
MORE_OK = {
    "x@xn--bcher-kva.example": "x@xn--bcher-kva.example",  # already punycode: unchanged
    "İ@example.com": "i@example.com",  # Go's simple lowercase, not Python's "i̇"
    "ΣΑΣ@example.com": "σασ@example.com",  # no final-sigma rule
    "a@straße.de": "a@xn--strae-oqa.de",  # non-transitional: ß is kept
    "　a@example.com ": "a@example.com",  # Unicode spaces are trimmed
    "a@EXAMPLE。com": "a@example.com",  # ideographic full stop maps to a dot
    "a@éa--b.com": "a@xn--a--b-9oa.com",  # 3rd and 4th bytes are "a-": allowed
    # Produced by the Go code (#52): UTS #46 allows these, so the engine must not refuse them.
    "a@😀.com": "a@xn--e28h.com",  # emoji domain
    "Love@❤.example": "love@xn--qei.example",
    "bob@例え.テスト": "bob@xn--r8jz45g.xn--zckzah",
    "a@b。c": "a@b.c",
}
MORE_BAD = [
    "a b@example.com",  # no-break space inside
    "a\x85@example.com",  # C1 control
    "a@b@example.com",  # a bare "@" in the local part
    "a@-example.com",  # label starts with a hyphen
    "a@exa_mple.com",  # STD3: no underscore
    "a@xn--zz.com",  # not valid punycode
    "é" * 33 + "@example.com",  # 66 bytes: Go counts bytes, not characters
    "a\x1f@example.com",  # Go does not trim C0 controls, it rejects them
    "a@xn--.com",  # an empty ACE label
    "a@xn--abc-.com",  # an ACE label that decodes to ASCII
    "a@é--b.com",  # Go checks "--" on the 3rd and 4th BYTES, here "--"
    # Refused by both sides (backend labelsOK / engine _labels_ok), #52.
    "x@xn--ﬁz.com",  # an "xn--" label with non-ASCII characters (ﬁ would map to "fi")
    "a@xn--Σ-.com",
    "x@XN--.com",  # a bare ACE prefix, any case
    "a@b..com",  # an empty label
]


class CanonicalEmailTest(unittest.TestCase):
    def test_idna_major_version_is_pinned(self):
        # email.py relies on idna's public API as of 3.x (requirements.txt pins idna<4).
        self.assertEqual(idna.__version__.split(".")[0], "3")

    def test_backend_vectors(self):
        for raw, want in {**BACKEND_OK, **MORE_OK}.items():
            with self.subTest(raw=raw):
                self.assertEqual(canonical_email(raw), want)
        for raw in BACKEND_BAD + MORE_BAD:
            with self.subTest(raw=raw):
                with self.assertRaises(InvalidEmail):
                    canonical_email(raw)

    def test_idempotent_and_lookup_helper(self):
        for want in {**BACKEND_OK, **MORE_OK}.values():
            self.assertEqual(canonical_email(want), want)
        self.assertIsNone(canonical_or_none("no-at"))
        self.assertTrue(issubclass(InvalidEmail, ValueError))  # pydantic turns it into a 422


if __name__ == "__main__":
    unittest.main()
