"""Unit tests for ``wd warm`` artifact sources (ADR 0067).

Covers the security-sensitive surface in :mod:`weld._warm_source`: SHA
validation, URL construction (template substitution + rejection of bad scheme /
credentials / non-SHA), the local-directory source, the HTTPS source exercised
over ``file://`` URLs (so the ``urlopen`` path is covered without a network),
and spec-to-source resolution.
"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path


from weld import _warm_source as ws  # noqa: E402

_SHA = "a" * 40
_SHA2 = "b" * 40
_GRAPH = b'{"nodes": {}, "edges": [], "meta": {}}'


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_store(root: Path, sha: str, data: bytes, *, tag: str | None) -> None:
    d = root / sha
    d.mkdir(parents=True, exist_ok=True)
    (d / "graph.json").write_bytes(data)
    if tag is not None:
        (d / "graph.json.sha256").write_text(tag, encoding="utf-8")


class IsValidShaTest(unittest.TestCase):
    def test_accepts_full_lowercase_hex(self) -> None:
        self.assertTrue(ws.is_valid_sha("0123456789abcdef" * 2 + "01234567"))

    def test_rejects_uppercase_short_and_nonhex(self) -> None:
        self.assertFalse(ws.is_valid_sha("A" * 40))
        self.assertFalse(ws.is_valid_sha("a" * 39))
        self.assertFalse(ws.is_valid_sha("a" * 41))
        self.assertFalse(ws.is_valid_sha("g" * 40))
        self.assertFalse(ws.is_valid_sha(""))
        self.assertFalse(ws.is_valid_sha(None))  # type: ignore[arg-type]


class BuildArtifactUrlTest(unittest.TestCase):
    def test_substitutes_validated_sha(self) -> None:
        url = ws.build_artifact_url("https://h/{sha}/graph.json", _SHA)
        self.assertEqual(url, f"https://h/{_SHA}/graph.json")

    def test_appends_fixed_suffix(self) -> None:
        url = ws.build_artifact_url(
            "https://h/{sha}/graph.json", _SHA, suffix=".sha256"
        )
        self.assertEqual(url, f"https://h/{_SHA}/graph.json.sha256")

    def test_rejects_non_sha(self) -> None:
        with self.assertRaises(ValueError):
            ws.build_artifact_url("https://h/{sha}", "../../etc/passwd")

    def test_rejects_missing_placeholder(self) -> None:
        with self.assertRaises(ValueError):
            ws.build_artifact_url("https://h/graph.json", _SHA)

    def test_rejects_non_https_scheme(self) -> None:
        for bad in ("http://h/{sha}", "ftp://h/{sha}", "ssh://h/{sha}"):
            with self.assertRaises(ValueError):
                ws.build_artifact_url(bad, _SHA)

    def test_allows_file_scheme_for_tests(self) -> None:
        url = ws.build_artifact_url("file:///tmp/{sha}/graph.json", _SHA)
        self.assertEqual(url, f"file:///tmp/{_SHA}/graph.json")

    def test_rejects_embedded_credentials(self) -> None:
        with self.assertRaises(ValueError):
            ws.build_artifact_url("https://user:pass@h/{sha}", _SHA)


class LocalDirSourceTest(unittest.TestCase):
    def test_hit_returns_bytes_and_tag(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_store(root, _SHA, _GRAPH, tag=_digest(_GRAPH))
            got = ws.LocalDirSource(root).fetch(_SHA)
            self.assertIsNotNone(got)
            data, tag = got  # type: ignore[misc]
            self.assertEqual(data, _GRAPH)
            self.assertEqual(tag, _digest(_GRAPH))

    def test_miss_when_absent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(ws.LocalDirSource(Path(td)).fetch(_SHA))

    def test_hit_without_tag_returns_none_tag(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_store(root, _SHA, _GRAPH, tag=None)
            got = ws.LocalDirSource(root).fetch(_SHA)
            self.assertIsNotNone(got)
            _, tag = got  # type: ignore[misc]
            self.assertIsNone(tag)

    def test_parses_sha256sum_two_column_form(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            tag_line = f"{_digest(_GRAPH)}  graph.json\n"
            _make_store(root, _SHA, _GRAPH, tag=tag_line)
            _, tag = ws.LocalDirSource(root).fetch(_SHA)  # type: ignore[misc]
            self.assertEqual(tag, _digest(_GRAPH))

    def test_invalid_sha_is_miss(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(ws.LocalDirSource(Path(td)).fetch("nope"))


class HttpsSourceFileUrlTest(unittest.TestCase):
    """Exercise the urlopen path over file:// so no network is needed."""

    def test_hit_over_file_url(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_store(root, _SHA, _GRAPH, tag=_digest(_GRAPH))
            template = f"file://{root}/{{sha}}/graph.json"
            got = ws.HttpsSource(template).fetch(_SHA)
            self.assertIsNotNone(got)
            data, tag = got  # type: ignore[misc]
            self.assertEqual(data, _GRAPH)
            self.assertEqual(tag, _digest(_GRAPH))

    def test_miss_over_file_url(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            template = f"file://{td}/{{sha}}/graph.json"
            self.assertIsNone(ws.HttpsSource(template).fetch(_SHA2))

    def test_bad_template_is_miss_not_raise(self) -> None:
        # No placeholder -> build_artifact_url raises -> fetch swallows -> miss.
        self.assertIsNone(ws.HttpsSource("https://h/graph.json").fetch(_SHA))


class SourceFromSpecTest(unittest.TestCase):
    def test_none_and_empty(self) -> None:
        self.assertIsNone(ws.source_from_spec(None))
        self.assertIsNone(ws.source_from_spec(""))

    def test_https_template(self) -> None:
        src = ws.source_from_spec("https://h/{sha}/graph.json")
        self.assertIsInstance(src, ws.HttpsSource)

    def test_https_without_placeholder_is_none(self) -> None:
        self.assertIsNone(ws.source_from_spec("https://h/graph.json"))

    def test_file_template_is_https_source(self) -> None:
        src = ws.source_from_spec("file:///t/{sha}/graph.json")
        self.assertIsInstance(src, ws.HttpsSource)

    def test_file_dir_is_local_dir_source(self) -> None:
        src = ws.source_from_spec("file:///t/store")
        self.assertIsInstance(src, ws.LocalDirSource)

    def test_bare_path_is_local_dir_source(self) -> None:
        src = ws.source_from_spec("/tmp/store")
        self.assertIsInstance(src, ws.LocalDirSource)

    def test_bare_template_without_scheme_is_none(self) -> None:
        # Ambiguous transport -> refuse rather than guess.
        self.assertIsNone(ws.source_from_spec("h/{sha}/graph.json"))


if __name__ == "__main__":
    unittest.main()
