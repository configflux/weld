"""Tests for the per-discover compiled-query cache on :class:`ParseCache`.

Hot path 3 of the eShopOnWeb cProfile baseline (docs/bench/
csharp-discover-cprofile-eshoponweb.md, post-b1uz): after the per-file
duplicate-parse elimination (0e7x + b1uz) landed, the dominant remaining
per-file cost is :func:`tree_sitter.Query` construction. ``Query(...)``
is a C-level call (~9ms per construction on the C# grammar). With 12
distinct queries per language and 254 .cs files in eShopOnWeb, the
strategy reconstructs the same compiled Query 4826 times across both
:func:`parse_file_symbols` and :func:`extract_call_edges` for the same
12 source strings -- a ~45s wall-clock waste.

The cache memoises the compiled :class:`tree_sitter.Query` keyed by
``(language, query_name)`` so each ``(language, query_name)`` pair is
compiled exactly once per discover run regardless of how many files
consume it. This test pins three contracts:

* :meth:`ParseCache.get_or_compile_query` returns the *same object*
  across repeated calls for one ``(language, query_name)`` pair --
  i.e. the underlying ``tree_sitter.Query(...)`` is called exactly
  once per pair.
* Distinct ``(language, query_name)`` pairs produce distinct entries
  (no key collision across languages or query names).
* End-to-end shape: when N files are processed via both
  :func:`parse_file_symbols` and :func:`extract_call_edges` against one
  shared cache, ``tree_sitter.Query(...)`` is invoked exactly once per
  unique ``(language, query_name)``, not once per file per query.

The cache lives on the per-discover :class:`ParseCache` (no module
globals) so ADR 0064 criterion 4 determinism is preserved.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class QueryCacheReturnsSameObjectTest(unittest.TestCase):
    """Repeat calls for one ``(language, query_name)`` must hit the memo."""

    def test_same_pair_returns_same_compiled_query(self) -> None:
        from weld.strategies import _ts_parse

        mock_ts = mock.MagicMock(name="tree_sitter")
        # Each Query(...) call returns a fresh sentinel; the cache hit
        # path must avoid creating a second one.
        mock_ts.Query.side_effect = lambda lang, src: mock.MagicMock(
            name=f"query({src!r})",
        )
        cache = _ts_parse.ParseCache()
        lang_obj = mock.MagicMock(name="language_obj")

        first = cache.get_or_compile_query(
            "csharp", "classes", "(class_declaration) @name",
            lang_obj, mock_ts,
        )
        second = cache.get_or_compile_query(
            "csharp", "classes", "(class_declaration) @name",
            lang_obj, mock_ts,
        )
        third = cache.get_or_compile_query(
            "csharp", "classes", "(class_declaration) @name",
            lang_obj, mock_ts,
        )

        self.assertIs(
            first, second,
            "second call for the same (language, query_name) must "
            "return the cached Query instance, not a fresh one",
        )
        self.assertIs(first, third)
        self.assertEqual(
            mock_ts.Query.call_count, 1,
            "tree_sitter.Query(...) must be constructed exactly once "
            "per (language, query_name) pair regardless of consumer count",
        )


class QueryCacheKeysAreDistinctTest(unittest.TestCase):
    """Different keys must not collide; each pair gets its own compiled Query."""

    def test_different_query_names_in_same_language_get_different_entries(
        self,
    ) -> None:
        from weld.strategies import _ts_parse

        mock_ts = mock.MagicMock(name="tree_sitter")
        mock_ts.Query.side_effect = lambda lang, src: mock.MagicMock(
            name=f"query({src!r})",
        )
        cache = _ts_parse.ParseCache()
        lang_obj = mock.MagicMock(name="language_obj")

        classes_q = cache.get_or_compile_query(
            "csharp", "classes", "(class_declaration) @name",
            lang_obj, mock_ts,
        )
        methods_q = cache.get_or_compile_query(
            "csharp", "methods", "(method_declaration) @name",
            lang_obj, mock_ts,
        )

        self.assertIsNot(
            classes_q, methods_q,
            "distinct query_names under the same language must NOT "
            "share a compiled Query (different S-expressions)",
        )
        self.assertEqual(
            mock_ts.Query.call_count, 2,
            "each unique (language, query_name) compiles once",
        )

    def test_same_query_name_across_languages_get_different_entries(
        self,
    ) -> None:
        from weld.strategies import _ts_parse

        mock_ts = mock.MagicMock(name="tree_sitter")
        mock_ts.Query.side_effect = lambda lang, src: mock.MagicMock(
            name=f"query({src!r})",
        )
        cache = _ts_parse.ParseCache()
        cs_lang_obj = mock.MagicMock(name="csharp_lang")
        java_lang_obj = mock.MagicMock(name="java_lang")

        cs_classes = cache.get_or_compile_query(
            "csharp", "classes", "(class_declaration) @name",
            cs_lang_obj, mock_ts,
        )
        java_classes = cache.get_or_compile_query(
            "java", "classes", "(class_declaration) @name",
            java_lang_obj, mock_ts,
        )

        self.assertIsNot(
            cs_classes, java_classes,
            "same query_name under different languages must compile "
            "separately -- the C-level Query object is bound to a "
            "Language and cannot be reused across grammars",
        )


class ParseFileSymbolsCompilesEachQueryOnceTest(unittest.TestCase):
    """``parse_file_symbols`` must consume the query cache across many files."""

    def _build_mock_tree_sitter(self):
        mock_ts = mock.MagicMock(name="tree_sitter")
        parser_instance = mock.MagicMock(name="parser")
        parser_instance.parse.return_value = mock.MagicMock(
            name="tree", root_node=mock.MagicMock(name="root"),
        )
        mock_ts.Parser.return_value = parser_instance
        mock_ts.Language.return_value = mock.MagicMock(name="language_obj")
        # Each Query construction is observable; the cache must collapse
        # repeats across files. Return a fresh sentinel per source so the
        # equality check below is meaningful.
        mock_ts.Query.side_effect = lambda lang, src: mock.MagicMock(
            name=f"query({src!r})",
        )
        cursor = mock.MagicMock(name="cursor")
        cursor.matches.return_value = []
        mock_ts.QueryCursor.return_value = cursor
        return mock_ts

    def test_n_files_compile_each_query_once(self) -> None:
        from weld.strategies import _ts_parse

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            files = []
            n = 5
            for i in range(n):
                f = root / f"F{i}.cs"
                f.write_text(f"class F{i} {{ }}\n", encoding="utf-8")
                files.append(f)

            queries = {
                "classes": "(class_declaration) @name",
                "methods": "(method_declaration) @name",
                "properties": "(property_declaration) @name",
            }

            mock_ts = self._build_mock_tree_sitter()
            cache = _ts_parse.ParseCache()
            loader = mock.MagicMock(return_value=object())

            original = sys.modules.get("tree_sitter")
            sys.modules["tree_sitter"] = mock_ts
            try:
                for f in files:
                    _ts_parse.parse_file_symbols(
                        f, "csharp", queries,
                        _language_loader=loader, cache=cache,
                    )
            finally:
                if original is not None:
                    sys.modules["tree_sitter"] = original
                else:
                    sys.modules.pop("tree_sitter", None)

            # 3 queries * 1 compile each = 3 total compiles, not 3 * 5 = 15
            self.assertEqual(
                mock_ts.Query.call_count, len(queries),
                "tree_sitter.Query(...) must be invoked exactly once per "
                "unique (language, query_name) across N files; got "
                f"{mock_ts.Query.call_count} (expected {len(queries)}) "
                f"-- query cache not consumed",
            )


class ExtractCallEdgesConsumesQueryCacheTest(unittest.TestCase):
    """``extract_call_edges`` must also memoize through the shared cache.

    Both passes (definition queries + ``calls``) must hit the cache the
    second time they encounter the same ``(language, query_name)`` pair
    -- whether triggered by the same file's sister consumer
    (parse_file_symbols already populated the cache for ``classes``,
    ``methods``, etc.) or by repeated file processing within
    extract_call_edges itself.
    """

    def _build_mock_tree_sitter(self):
        mock_ts = mock.MagicMock(name="tree_sitter")
        parser_instance = mock.MagicMock(name="parser")
        parser_instance.parse.return_value = mock.MagicMock(
            name="tree", root_node=mock.MagicMock(name="root"),
        )
        mock_ts.Parser.return_value = parser_instance
        mock_ts.Language.return_value = mock.MagicMock(name="language_obj")
        mock_ts.Query.side_effect = lambda lang, src: mock.MagicMock(
            name=f"query({src!r})",
        )
        cursor = mock.MagicMock(name="cursor")
        cursor.matches.return_value = []
        mock_ts.QueryCursor.return_value = cursor
        return mock_ts

    def test_call_edges_cache_consumed_after_parse_symbols(self) -> None:
        from weld.strategies import _ts_call_graph, _ts_parse

        with tempfile.TemporaryDirectory() as td:
            file_path = Path(td) / "Service.cs"
            file_path.write_text(
                "public class Service { public void Run() { } }\n",
                encoding="utf-8",
            )

            mock_ts = self._build_mock_tree_sitter()
            cache = _ts_parse.ParseCache()
            loader = mock.MagicMock(return_value=object())

            queries = {
                "classes": "(class_declaration name: (identifier) @name)",
                "interfaces": "(interface_declaration name: (identifier) @name)",
                "structs": "(struct_declaration name: (identifier) @name)",
                "records": "(record_declaration name: (identifier) @name)",
                "methods": "(method_declaration name: (identifier) @name)",
                "properties": "(property_declaration name: (identifier) @name)",
                "calls": "(invocation_expression function: (identifier) @name)",
            }

            original = sys.modules.get("tree_sitter")
            sys.modules["tree_sitter"] = mock_ts
            try:
                # First pass populates the cache with all queries that
                # parse_file_symbols runs (here: every key in ``queries``
                # except ``calls`` would be the natural set; we pass all
                # of them so the cache is fully warm).
                _ts_parse.parse_file_symbols(
                    file_path, "csharp", queries,
                    _language_loader=loader, cache=cache,
                )
                compiles_after_parse = mock_ts.Query.call_count
                # Second pass through extract_call_edges. Every definition
                # query is already cached; only ``calls`` is new.
                _ts_call_graph.extract_call_edges(
                    file_path=file_path, rel_path="Service.cs",
                    language="csharp", queries=queries, cache=cache,
                )
            finally:
                if original is not None:
                    sys.modules["tree_sitter"] = original
                else:
                    sys.modules.pop("tree_sitter", None)

            # The 7 queries are: 6 def + 1 calls. parse_file_symbols
            # compiles all 7 (it iterates every key in ``queries``).
            # extract_call_edges sees them all in the cache already and
            # MUST NOT trigger any further Query(...) construction.
            self.assertEqual(
                compiles_after_parse, len(queries),
                "parse_file_symbols should compile every query in its "
                "input dict exactly once via the cache",
            )
            self.assertEqual(
                mock_ts.Query.call_count, len(queries),
                "extract_call_edges must consume the shared cache; it "
                "should NOT trigger any further Query(...) calls when "
                "the cache already holds every (language, query_name) "
                "it needs",
            )


class TwoPassNFilesQueryCacheShapeTest(unittest.TestCase):
    """End-to-end shape: N files through both passes = K query compiles, not K*N."""

    def test_n_files_through_both_passes_compile_each_query_once(self) -> None:
        from weld.strategies import _ts_call_graph, _ts_parse

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            files = []
            n = 8  # representative; production has 254 -- shape is identical
            for i in range(n):
                f = root / f"F{i}.cs"
                f.write_text(f"public class F{i} {{ }}\n", encoding="utf-8")
                files.append(f)

            mock_ts = mock.MagicMock(name="tree_sitter")
            mock_ts.Parser.return_value.parse.return_value = mock.MagicMock(
                root_node=mock.MagicMock(),
            )
            mock_ts.Language.return_value = mock.MagicMock()
            mock_ts.Query.side_effect = lambda lang, src: mock.MagicMock(
                name=f"query({src!r})",
            )
            mock_ts.QueryCursor.return_value.matches.return_value = []

            queries = {
                "classes": "(class_declaration name: (identifier) @name)",
                "interfaces": "(interface_declaration name: (identifier) @name)",
                "structs": "(struct_declaration name: (identifier) @name)",
                "records": "(record_declaration name: (identifier) @name)",
                "methods": "(method_declaration name: (identifier) @name)",
                "properties": "(property_declaration name: (identifier) @name)",
                "calls": "(invocation_expression function: (identifier) @name)",
            }
            unique_queries = len(queries)

            cache = _ts_parse.ParseCache()
            loader = mock.MagicMock(return_value=object())

            original = sys.modules.get("tree_sitter")
            sys.modules["tree_sitter"] = mock_ts
            try:
                for f in files:
                    rel = f.name
                    _ts_parse.parse_file_symbols(
                        f, "csharp", queries,
                        _language_loader=loader, cache=cache,
                    )
                    _ts_call_graph.extract_call_edges(
                        file_path=f, rel_path=rel,
                        language="csharp", queries=queries, cache=cache,
                    )
            finally:
                if original is not None:
                    sys.modules["tree_sitter"] = original
                else:
                    sys.modules.pop("tree_sitter", None)

            self.assertEqual(
                mock_ts.Query.call_count, unique_queries,
                f"tree_sitter.Query(...) must be invoked exactly "
                f"{unique_queries} times across {n} files through both "
                f"passes; got {mock_ts.Query.call_count}. Regression: "
                f"compiled-query cache not consumed -- compiled queries "
                f"are being rebuilt per file (eShopOnWeb hot path 3).",
            )


if __name__ == "__main__":
    unittest.main()
