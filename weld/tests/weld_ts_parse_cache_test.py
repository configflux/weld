"""Tests for the per-discover tree-sitter parse cache.

The cache must:

* Memoize the per-language ``tree_sitter.Language`` so the grammar
  loader is called once per language per cache instance (not once per
  file).
* Memoize the per-file parse output keyed by ``(abs_path, mtime,
  language)`` so a second ``parse_file_symbols`` call for the same
  unchanged file does not re-read disk or re-run ``parser.parse``.
* Treat mtime as part of the key so a file modified during the same
  discovery run is re-parsed (defensive; ``wd discover`` walks the file
  set once today but the contract must hold).
* Be a plain instance attached to ``context``; module-level globals are
  not used so test isolation and determinism are preserved.
* Continue to honour the ``_language_loader`` injection point used by
  ``weld/tests/tree_sitter_strategy_test.py``.

The cache stores the parsed ``tree_sitter.Tree`` and the decoded
``source_bytes`` so a sister consumer (e.g. ``extract_call_edges``)
can drop a second parse pass by consuming the same entry. This test
only exercises the seam; wiring the consumer is a separate change.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock


class ParseCacheLoaderMemoizationTest(unittest.TestCase):
    """The Language object must be built once per language per cache."""

    def test_loader_called_once_for_many_files(self) -> None:
        from weld.strategies import _ts_parse

        loader = mock.MagicMock(return_value=object())

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            files = []
            for i in range(5):
                f = root / f"f{i}.cs"
                f.write_text(f"class C{i} {{ }}\n")
                files.append(f)

            mock_ts = mock.MagicMock()
            mock_ts.Language.return_value = object()
            mock_ts.Parser.return_value.parse.return_value = mock.MagicMock(
                root_node=mock.MagicMock(),
            )
            mock_ts.QueryCursor.return_value.matches.return_value = []

            cache = _ts_parse.ParseCache()

            import sys
            original = sys.modules.get("tree_sitter")
            sys.modules["tree_sitter"] = mock_ts
            try:
                for f in files:
                    _ts_parse.parse_file_symbols(
                        f, "csharp", {},
                        _language_loader=loader,
                        cache=cache,
                    )
            finally:
                if original is not None:
                    sys.modules["tree_sitter"] = original
                else:
                    sys.modules.pop("tree_sitter", None)

            self.assertEqual(
                loader.call_count, 1,
                "language loader should be called exactly once per "
                "language across many files when a cache is supplied",
            )
            self.assertEqual(
                mock_ts.Language.call_count, 1,
                "tree_sitter.Language should be constructed once per "
                "language across many files",
            )
            self.assertEqual(
                mock_ts.Parser.call_count, 1,
                "tree_sitter.Parser should be constructed once per "
                "language across many files",
            )


class ParseCacheTreeReuseTest(unittest.TestCase):
    """A second call for the same (abs_path, mtime, language) is a hit."""

    def test_second_call_does_not_reparse(self) -> None:
        from weld.strategies import _ts_parse

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            f = root / "example.cs"
            f.write_text("class A { }\n")

            mock_ts = mock.MagicMock()
            mock_ts.Language.return_value = object()
            parser = mock_ts.Parser.return_value
            parser.parse.return_value = mock.MagicMock(
                root_node=mock.MagicMock(),
            )
            mock_ts.QueryCursor.return_value.matches.return_value = []

            cache = _ts_parse.ParseCache()
            loader = mock.MagicMock(return_value=object())

            import sys
            original = sys.modules.get("tree_sitter")
            sys.modules["tree_sitter"] = mock_ts
            try:
                _ts_parse.parse_file_symbols(
                    f, "csharp", {},
                    _language_loader=loader, cache=cache,
                )
                _ts_parse.parse_file_symbols(
                    f, "csharp", {},
                    _language_loader=loader, cache=cache,
                )
            finally:
                if original is not None:
                    sys.modules["tree_sitter"] = original
                else:
                    sys.modules.pop("tree_sitter", None)

            self.assertEqual(
                parser.parse.call_count, 1,
                "parser.parse should run once for two calls on the "
                "same unchanged file when the cache is shared",
            )


class ParseCacheGetParseTest(unittest.TestCase):
    """The cache exposes get_parse() for downstream consumers."""

    def test_get_parse_returns_tree_and_bytes(self) -> None:
        from weld.strategies import _ts_parse

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            f = root / "example.cs"
            f.write_text("class A { }\n")

            fake_tree = mock.MagicMock(root_node=mock.MagicMock())
            mock_ts = mock.MagicMock()
            mock_ts.Language.return_value = object()
            mock_ts.Parser.return_value.parse.return_value = fake_tree
            mock_ts.QueryCursor.return_value.matches.return_value = []

            cache = _ts_parse.ParseCache()
            loader = mock.MagicMock(return_value=object())

            import sys
            original = sys.modules.get("tree_sitter")
            sys.modules["tree_sitter"] = mock_ts
            try:
                _ts_parse.parse_file_symbols(
                    f, "csharp", {},
                    _language_loader=loader, cache=cache,
                )
                entry = cache.get_parse(f, "csharp")
            finally:
                if original is not None:
                    sys.modules["tree_sitter"] = original
                else:
                    sys.modules.pop("tree_sitter", None)

            self.assertIsNotNone(
                entry,
                "get_parse must return the cached entry after a parse",
            )
            self.assertIs(
                entry.tree, fake_tree,
                "cached entry must surface the actual tree_sitter.Tree",
            )
            self.assertEqual(
                entry.source_bytes, b"class A { }\n",
                "cached entry must surface the decoded source bytes",
            )
            self.assertIsNotNone(
                entry.language_obj,
                "cached entry must surface the per-language Language",
            )
            self.assertIsNotNone(
                entry.parser,
                "cached entry must surface the per-language Parser",
            )

    def test_get_parse_miss_returns_none(self) -> None:
        from weld.strategies import _ts_parse

        cache = _ts_parse.ParseCache()
        self.assertIsNone(cache.get_parse(Path("/does/not/exist"), "csharp"))


class ParseCacheMtimeKeyTest(unittest.TestCase):
    """A file modified during the run must be re-parsed."""

    def test_mtime_change_invalidates_entry(self) -> None:
        import os

        from weld.strategies import _ts_parse

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            f = root / "example.cs"
            f.write_text("class A { }\n")

            mock_ts = mock.MagicMock()
            mock_ts.Language.return_value = object()
            parser = mock_ts.Parser.return_value
            parser.parse.return_value = mock.MagicMock(
                root_node=mock.MagicMock(),
            )
            mock_ts.QueryCursor.return_value.matches.return_value = []

            cache = _ts_parse.ParseCache()
            loader = mock.MagicMock(return_value=object())

            import sys
            original = sys.modules.get("tree_sitter")
            sys.modules["tree_sitter"] = mock_ts
            try:
                _ts_parse.parse_file_symbols(
                    f, "csharp", {},
                    _language_loader=loader, cache=cache,
                )
                # Bump mtime forward; content change irrelevant for the
                # key.
                st = f.stat()
                os.utime(f, (st.st_atime, st.st_mtime + 5))
                _ts_parse.parse_file_symbols(
                    f, "csharp", {},
                    _language_loader=loader, cache=cache,
                )
            finally:
                if original is not None:
                    sys.modules["tree_sitter"] = original
                else:
                    sys.modules.pop("tree_sitter", None)

            self.assertEqual(
                parser.parse.call_count, 2,
                "an mtime change must invalidate the cached parse",
            )


class ParseCacheContextScopeTest(unittest.TestCase):
    """A cache obtained from one context is independent from another."""

    def test_separate_contexts_have_separate_caches(self) -> None:
        from weld.strategies import _ts_parse

        ctx_a: dict = {}
        ctx_b: dict = {}
        cache_a = _ts_parse.get_parse_cache(ctx_a)
        cache_b = _ts_parse.get_parse_cache(ctx_b)
        self.assertIsNot(
            cache_a, cache_b,
            "each per-discover context must own a distinct cache",
        )
        # Idempotent: a second fetch from the same context returns the
        # same instance (so all sources share state within one discover).
        self.assertIs(
            cache_a, _ts_parse.get_parse_cache(ctx_a),
            "get_parse_cache must be idempotent per context",
        )


class ParseCacheBackwardCompatTest(unittest.TestCase):
    """Calling parse_file_symbols without a cache keeps the old behaviour."""

    def test_no_cache_still_parses(self) -> None:
        from weld.strategies import _ts_parse

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            f = root / "example.cs"
            f.write_text("class A { }\n")

            mock_ts = mock.MagicMock()
            mock_ts.Language.return_value = object()
            mock_ts.Parser.return_value.parse.return_value = mock.MagicMock(
                root_node=mock.MagicMock(),
            )
            mock_ts.QueryCursor.return_value.matches.return_value = []

            loader = mock.MagicMock(return_value=object())

            import sys
            original = sys.modules.get("tree_sitter")
            sys.modules["tree_sitter"] = mock_ts
            try:
                # No cache argument; should still work and call into
                # the loader exactly once for this single file.
                _ts_parse.parse_file_symbols(
                    f, "csharp", {},
                    _language_loader=loader,
                )
            finally:
                if original is not None:
                    sys.modules["tree_sitter"] = original
                else:
                    sys.modules.pop("tree_sitter", None)

            self.assertEqual(loader.call_count, 1)


if __name__ == "__main__":
    unittest.main()
