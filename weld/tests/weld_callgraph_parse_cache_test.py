"""Tests pinning ``extract_call_edges`` consumes the per-discover parse cache.

Hot path 2 of the eShopOnWeb cProfile baseline
(``docs/bench/csharp-discover-cprofile-eshoponweb.md``) showed
``tree_sitter.Parser.parse`` running 508 times for a 254-file corpus: once
inside :func:`weld.strategies._ts_parse.parse_file_symbols` and once inside
:func:`weld.strategies._ts_call_graph.extract_call_edges`. A prior
optimization introduced the per-discover :class:`_ts_parse.ParseCache`.
This test pins the call-site fix: when both helpers run against the same
cache for the same unchanged file, the second call must consume the
cached :class:`_ts_parse.ParseEntry` (cached tree + source bytes) rather
than reparsing.

The observable contract asserted directly: ``parser.parse`` is called
exactly once per file across both passes.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class _FakeNode:
    """Minimal stand-in for a tree-sitter ``Node`` returned by query matches."""

    def __init__(self, text: bytes, start_point=(0, 0), end_point=(0, 0)) -> None:
        self.text = text
        self.start_point = start_point
        self.end_point = end_point


class CallGraphParseCacheHotPath2Test(unittest.TestCase):
    """``extract_call_edges`` must consume the cache produced by ``parse_file_symbols``.

    When the cache already holds a parse entry for ``(file, mtime, lang)``,
    the call-graph helper must not call ``parser.parse`` again -- it must
    reuse the cached :class:`tree_sitter.Tree`.
    """

    def _build_mock_tree_sitter(self):
        """Construct a ``tree_sitter`` stand-in with deterministic cursors."""
        mock_ts = mock.MagicMock(name="tree_sitter")
        parser_instance = mock.MagicMock(name="parser")
        parser_instance.parse.return_value = mock.MagicMock(
            name="tree", root_node=mock.MagicMock(name="root"),
        )
        mock_ts.Parser.return_value = parser_instance
        mock_ts.Language.return_value = mock.MagicMock(name="language_obj")
        mock_ts.Query.return_value = mock.MagicMock(name="query")

        # parse_file_symbols runs one QueryCursor per registered query
        # (here: only ``methods``). extract_call_edges runs one cursor
        # per definition query name (csharp = 6 buckets) plus one for
        # ``calls``. All cursors yield zero matches; the cache assertion
        # is about ``parser.parse`` call count, not query output.
        cursor = mock.MagicMock(name="cursor")
        cursor.matches.return_value = []
        mock_ts.QueryCursor.return_value = cursor
        return mock_ts, parser_instance

    def test_extract_call_edges_consumes_cached_parse(self) -> None:
        from weld.strategies import _ts_call_graph, _ts_parse

        with tempfile.TemporaryDirectory() as td:
            file_path = Path(td) / "Service.cs"
            file_path.write_text(
                "public class Service { public void Run() { } }\n",
                encoding="utf-8",
            )

            mock_ts, parser_instance = self._build_mock_tree_sitter()
            cache = _ts_parse.ParseCache()
            loader = mock.MagicMock(return_value=object())

            original = sys.modules.get("tree_sitter")
            sys.modules["tree_sitter"] = mock_ts
            try:
                # First pass: parse_file_symbols populates the cache.
                _ts_parse.parse_file_symbols(
                    file_path, "csharp",
                    {"methods": "(method_declaration) @name"},
                    _language_loader=loader, cache=cache,
                )
                # Second pass: extract_call_edges must consume the cache.
                _ts_call_graph.extract_call_edges(
                    file_path=file_path,
                    rel_path="Service.cs",
                    language="csharp",
                    queries={
                        "calls": "(invocation_expression) @name",
                        "methods": "(method_declaration) @name",
                    },
                    cache=cache,
                )
            finally:
                if original is not None:
                    sys.modules["tree_sitter"] = original
                else:
                    sys.modules.pop("tree_sitter", None)

            self.assertEqual(
                parser_instance.parse.call_count, 1,
                "parser.parse must run once across parse_file_symbols + "
                "extract_call_edges when a shared cache is supplied; "
                "the second pass should consume the cached tree",
            )

    def test_extract_call_edges_without_cache_still_parses(self) -> None:
        """Backward-compat: omitting the cache keeps the original behaviour."""
        from weld.strategies import _ts_call_graph

        with tempfile.TemporaryDirectory() as td:
            file_path = Path(td) / "Service.cs"
            file_path.write_text(
                "public class Service { public void Run() { } }\n",
                encoding="utf-8",
            )

            mock_ts, parser_instance = self._build_mock_tree_sitter()

            original = sys.modules.get("tree_sitter")
            sys.modules["tree_sitter"] = mock_ts
            try:
                with mock.patch.object(
                    _ts_call_graph, "load_ts_language",
                    return_value=object(),
                ):
                    _ts_call_graph.extract_call_edges(
                        file_path=file_path,
                        rel_path="Service.cs",
                        language="csharp",
                        queries={
                            "calls": "(invocation_expression) @name",
                            "methods": "(method_declaration) @name",
                        },
                    )
            finally:
                if original is not None:
                    sys.modules["tree_sitter"] = original
                else:
                    sys.modules.pop("tree_sitter", None)

            self.assertEqual(
                parser_instance.parse.call_count, 1,
                "parser.parse must still run once when no cache is supplied",
            )

    def test_extract_call_edges_cache_miss_falls_back_to_parse(self) -> None:
        """A fresh cache (no prior parse) must still produce a tree.

        Defensive: even though the strategy call-site always runs
        ``parse_file_symbols`` first, downstream consumers might invoke
        ``extract_call_edges`` directly with a fresh cache. The helper
        must handle the miss path by parsing and storing the entry.
        """
        from weld.strategies import _ts_call_graph, _ts_parse

        with tempfile.TemporaryDirectory() as td:
            file_path = Path(td) / "Service.cs"
            file_path.write_text(
                "public class Service { }\n", encoding="utf-8",
            )

            mock_ts, parser_instance = self._build_mock_tree_sitter()
            cache = _ts_parse.ParseCache()

            original = sys.modules.get("tree_sitter")
            sys.modules["tree_sitter"] = mock_ts
            try:
                with mock.patch.object(
                    _ts_call_graph, "load_ts_language",
                    return_value=object(),
                ):
                    _ts_call_graph.extract_call_edges(
                        file_path=file_path,
                        rel_path="Service.cs",
                        language="csharp",
                        queries={
                            "calls": "(invocation_expression) @name",
                            "methods": "(method_declaration) @name",
                        },
                        cache=cache,
                    )
            finally:
                if original is not None:
                    sys.modules["tree_sitter"] = original
                else:
                    sys.modules.pop("tree_sitter", None)

            self.assertEqual(
                parser_instance.parse.call_count, 1,
                "parser.parse must run on cache miss to populate the entry",
            )
            # After the call, the cache holds an entry for this file.
            self.assertIsNotNone(
                cache.get_parse(file_path, "csharp"),
                "cache miss path must populate the cache for subsequent reuse",
            )


class CallGraphParseCacheTwoPassN254Test(unittest.TestCase):
    """End-to-end shape: N files through both passes = N parses, not 2N."""

    def test_n_files_through_both_passes_only_parse_once_each(self) -> None:
        from weld.strategies import _ts_call_graph, _ts_parse

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            files: list[Path] = []
            n = 8  # representative; production has 254 -- the shape is identical
            for i in range(n):
                f = root / f"F{i}.cs"
                f.write_text(f"public class F{i} {{ }}\n", encoding="utf-8")
                files.append(f)

            mock_ts = mock.MagicMock(name="tree_sitter")
            parser_instance = mock.MagicMock(name="parser")
            parser_instance.parse.return_value = mock.MagicMock(
                root_node=mock.MagicMock(),
            )
            mock_ts.Parser.return_value = parser_instance
            mock_ts.Language.return_value = mock.MagicMock()
            mock_ts.QueryCursor.return_value.matches.return_value = []

            cache = _ts_parse.ParseCache()
            loader = mock.MagicMock(return_value=object())

            original = sys.modules.get("tree_sitter")
            sys.modules["tree_sitter"] = mock_ts
            try:
                for f in files:
                    rel = f.name
                    _ts_parse.parse_file_symbols(
                        f, "csharp",
                        {"methods": "(method_declaration) @name"},
                        _language_loader=loader, cache=cache,
                    )
                    _ts_call_graph.extract_call_edges(
                        file_path=f,
                        rel_path=rel,
                        language="csharp",
                        queries={
                            "calls": "(invocation_expression) @name",
                            "methods": "(method_declaration) @name",
                        },
                        cache=cache,
                    )
            finally:
                if original is not None:
                    sys.modules["tree_sitter"] = original
                else:
                    sys.modules.pop("tree_sitter", None)

            self.assertEqual(
                parser_instance.parse.call_count, n,
                f"parser.parse must run exactly {n} times across {n} "
                f"files through both passes; got "
                f"{parser_instance.parse.call_count} (regression: hot "
                f"path 2 duplicate parse not eliminated)",
            )


if __name__ == "__main__":
    unittest.main()
