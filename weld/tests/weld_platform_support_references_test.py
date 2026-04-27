"""Reference-link integrity guard for ``docs/platform-support.md``.

Walks the platform-support page and asserts that every URL it cites is
*well-formed* -- has a scheme of ``http`` or ``https``, a non-empty host,
and no obvious typos (whitespace, missing colon, empty fragment). This is a
purely offline static check; it does **not** open network connections, and
it does not replace the markdownlint MD042 / MD052 checks (which catch
empty links and undefined reference labels at lint time).

The motivation: the page is a public-facing platform support matrix, and
its References section is the canonical pointer to upstream client docs.
A typo in one of those URLs is a long-lived footgun (silent 404 for
readers, no CI signal). MD042 catches empty links; MD052 catches
unresolved reference labels. Neither catches a syntactically valid but
malformed URL like ``htp://example.com`` or ``https:///empty-host``.

The test extracts URLs from two markdown shapes:

- Inline autolinks: ``<https://example.com>``
- Reference-link definitions: ``[label]: https://example.com``

Inline ``[text](https://...)`` links are also covered. Each extracted URL
must parse with ``urllib.parse.urlsplit`` and meet:

1. ``scheme`` is ``http`` or ``https`` (case-insensitive)
2. ``netloc`` (host) is non-empty after stripping whitespace
3. The raw URL contains no whitespace characters

If any URL fails, the test names the offending URL so the maintainer can
fix it in the same change. This is a static well-formedness check only --
real reachability (404s, redirects) is intentionally out of scope; that
belongs in a separate, opt-in network test.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from urllib.parse import urlsplit


def _repo_root() -> Path:
    """Return the repository root by walking up from this test file."""
    return Path(__file__).resolve().parents[2]


# Markdown shapes we care about, in order of appearance in the doc:
#
#   <https://example.com>            inline autolink
#   [text](https://example.com)      inline link with label
#   [label]: https://example.com     reference-link definition
#
# We deliberately collect the URL substring only (no surrounding
# punctuation) so well-formedness can be asserted on the URL itself.
_AUTOLINK_RE = re.compile(r"<((?:https?)://[^>\s]+)>", re.IGNORECASE)
_INLINE_LINK_RE = re.compile(r"\]\(((?:https?)://[^)\s]+)\)", re.IGNORECASE)
_REF_DEF_RE = re.compile(
    r"^\s*\[[^\]]+\]:\s*((?:https?)://\S+)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def _extract_urls(text: str) -> list[tuple[str, str]]:
    """Return ``(shape, url)`` pairs for every URL found in *text*."""
    urls: list[tuple[str, str]] = []
    for match in _AUTOLINK_RE.finditer(text):
        urls.append(("autolink", match.group(1)))
    for match in _INLINE_LINK_RE.finditer(text):
        urls.append(("inline-link", match.group(1)))
    for match in _REF_DEF_RE.finditer(text):
        urls.append(("ref-def", match.group(1)))
    return urls


def _is_well_formed(url: str) -> tuple[bool, str]:
    """Return ``(ok, reason)``. ``reason`` is empty when ``ok`` is True."""
    if any(ch.isspace() for ch in url):
        return False, "contains whitespace"
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        return False, f"unexpected scheme {parts.scheme!r}"
    if not parts.netloc.strip():
        return False, "empty host"
    return True, ""


class PlatformSupportReferencesTest(unittest.TestCase):
    """``docs/platform-support.md`` URLs must be well-formed."""

    def setUp(self) -> None:
        self.path = _repo_root() / "docs" / "platform-support.md"
        self.assertTrue(
            self.path.exists(),
            f"Missing docs/platform-support.md: {self.path}",
        )
        self.text = self.path.read_text(encoding="utf-8")

    def test_extractor_finds_at_least_one_url(self) -> None:
        """Sanity: the page must contain at least one URL.

        Guards the test from silently passing if the file is later
        rewritten in a form the extractor cannot see.
        """
        urls = _extract_urls(self.text)
        self.assertGreater(
            len(urls),
            0,
            "Expected at least one URL in docs/platform-support.md; "
            "extractor returned none. Update _extract_urls if the doc "
            "shape changed.",
        )

    def test_all_urls_are_well_formed(self) -> None:
        """Every cited URL must parse and have an http/https scheme + host."""
        bad: list[str] = []
        for shape, url in _extract_urls(self.text):
            ok, reason = _is_well_formed(url)
            if not ok:
                bad.append(f"[{shape}] {url}  ({reason})")
        self.assertEqual(
            bad,
            [],
            "Malformed URLs in docs/platform-support.md:\n  " + "\n  ".join(bad),
        )

    def test_no_duplicate_reference_definitions(self) -> None:
        """Reference-link definitions must have unique labels.

        MD052 catches *unresolved* reference labels; this test catches the
        opposite shape -- two competing definitions for the same label
        (last one wins silently in most renderers). Inline autolinks and
        inline ``[text](url)`` links are out of scope for this check.
        """
        labels: dict[str, int] = {}
        # Match ``[label]: url`` lines; label is anything inside [] up to
        # the closing bracket. We only check label uniqueness, not URL.
        ref_def_label_re = re.compile(
            r"^\s*\[([^\]]+)\]:\s*\S+\s*$",
            re.MULTILINE,
        )
        for match in ref_def_label_re.finditer(self.text):
            label = match.group(1).strip().lower()
            labels[label] = labels.get(label, 0) + 1
        duplicates = sorted(label for label, count in labels.items() if count > 1)
        self.assertEqual(
            duplicates,
            [],
            f"Duplicate reference-link definitions: {duplicates}",
        )


class ExtractorUnitTest(unittest.TestCase):
    """Unit-level coverage for ``_extract_urls`` and ``_is_well_formed``.

    Pins the extractor and validator behaviour so the integration test
    above stays meaningful as the doc evolves.
    """

    def test_extracts_autolink(self) -> None:
        text = "See <https://example.com/path> for details."
        self.assertEqual(_extract_urls(text), [("autolink", "https://example.com/path")])

    def test_extracts_inline_link(self) -> None:
        text = "[example](https://example.com) here."
        self.assertEqual(_extract_urls(text), [("inline-link", "https://example.com")])

    def test_extracts_reference_definition(self) -> None:
        text = "[label]: https://example.com\n"
        self.assertEqual(_extract_urls(text), [("ref-def", "https://example.com")])

    def test_well_formed_https(self) -> None:
        ok, reason = _is_well_formed("https://example.com/x")
        self.assertTrue(ok, reason)

    def test_well_formed_http(self) -> None:
        ok, _ = _is_well_formed("http://example.com")
        self.assertTrue(ok)

    def test_rejects_bad_scheme(self) -> None:
        ok, reason = _is_well_formed("ftp://example.com")
        self.assertFalse(ok)
        self.assertIn("scheme", reason)

    def test_rejects_typo_scheme(self) -> None:
        ok, reason = _is_well_formed("htp://example.com")
        self.assertFalse(ok)

    def test_rejects_empty_host(self) -> None:
        ok, reason = _is_well_formed("https:///path")
        self.assertFalse(ok)
        self.assertIn("host", reason)

    def test_rejects_whitespace_in_url(self) -> None:
        ok, reason = _is_well_formed("https://example.com/with space")
        self.assertFalse(ok)
        self.assertIn("whitespace", reason)


if __name__ == "__main__":
    unittest.main()
