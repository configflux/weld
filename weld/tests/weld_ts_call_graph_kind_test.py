"""Tests for synthetic ``kind`` properties on tree-sitter call-graph sentinels.

The shared tree-sitter call-graph helper (``_ts_call_graph.extract_call_edges``)
mints two synthetic ``symbol`` node shapes that are not real source-code
definitions:

1. **File-scope caller sentinel** -- ``symbol:<lang>:<module>:<file>``.
   Owns every call site in a file when we cannot attribute the caller
   to a specific enclosing definition (tree-sitter does not give us
   scope tracking for free).
2. **Unresolved callee sentinel** -- ``symbol:unresolved:<name>``.
   The other end of every ``calls`` edge before cross-file resolution
   upgrades it.

ADR 0064 criterion 2 (kind coverage) requires at least 80% of C#
symbol nodes to carry a ``kind`` property. Without explicit kinds on
the two sentinels above, every C# file contributes one un-kinded
file-sentinel + N un-kinded unresolved callees, dragging coverage to
~60% on a real ASP.NET Core corpus.

This test pins:

* file-scope caller sentinels carry ``kind="file"``.
* unresolved call-site sentinels carry ``kind="unresolved"``.

The kinds are *synthetic* (weld modelling layer, not source-code
vocabulary). They are deliberately excluded from the tier_check
criterion-1 vocabulary tally via ``tier_check_kinds._SYNTHETIC_KINDS``
-- that wiring is covered by ``tier_check_test.py``.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


from weld.strategies import _ts_call_graph  # noqa: E402


class _FakeNode:
    """Minimal stand-in for a tree-sitter ``Node`` returned by query matches."""

    def __init__(self, text: bytes, start_point=(0, 0), end_point=(0, 0)) -> None:
        self.text = text
        self.start_point = start_point
        self.end_point = end_point


def _patched_extract(
    *,
    definitions: list[str],
    call_names: list[str],
):
    """Patch ``extract_call_edges``'s tree-sitter dependencies.

    Returns a context-manager that mocks the parser, language loader,
    and ``QueryCursor.matches`` so we can drive the helper end-to-end
    without a real tree-sitter installation.
    """
    import contextlib

    @contextlib.contextmanager
    def _cm():
        # Stand-in tree-sitter module: just enough surface area for the
        # helper's ``import tree_sitter`` and ``tree_sitter.Language(...)``
        # / ``tree_sitter.Parser(...)`` / ``tree_sitter.Query(...)``
        # / ``tree_sitter.QueryCursor(...)`` call sites to succeed.
        ts_module = mock.MagicMock(name="tree_sitter")

        # Parser().parse(bytes) -> tree (only ``.root_node`` used).
        parser_instance = mock.MagicMock(name="parser")
        parser_instance.parse.return_value = mock.MagicMock(name="tree")
        ts_module.Parser.return_value = parser_instance
        ts_module.Language.return_value = mock.MagicMock(name="language_obj")
        ts_module.Query.return_value = mock.MagicMock(name="query")

        # The helper iterates ``definitions`` first then ``calls`` against
        # separate ``QueryCursor`` instances. To distinguish them we read
        # the constructed Query's identity from a side-channel: instead
        # we control the order by returning two different cursor mocks.
        def_cursor = mock.MagicMock(name="def_cursor")
        call_cursor = mock.MagicMock(name="call_cursor")

        def _matches_for_defs(_root):
            return [
                (0, {"name": [_FakeNode(name.encode("utf-8"))]})
                for name in definitions
            ]

        def _matches_for_calls(_root):
            return [
                (0, {"name": [_FakeNode(name.encode("utf-8"))]})
                for name in call_names
            ]

        def_cursor.matches.side_effect = _matches_for_defs
        call_cursor.matches.side_effect = _matches_for_calls
        ts_module.QueryCursor.side_effect = [def_cursor, call_cursor]

        # Stub the language loader so the helper does not look for a
        # native grammar wheel.
        with mock.patch.dict(sys.modules, {"tree_sitter": ts_module}), \
            mock.patch.object(
                _ts_call_graph,
                "load_ts_language",
                return_value=object(),
        ):
            yield


    return _cm()


class FileCallerSentinelKindTest(unittest.TestCase):
    """The synthetic file-scope caller carries ``kind="file"``."""

    def test_csharp_file_caller_has_kind_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            file_path = Path(td) / "OrdersService.cs"
            file_path.write_text("void Foo() { Bar(); }\n", encoding="utf-8")
            with _patched_extract(
                definitions=["Foo"],
                call_names=["Bar"],
            ):
                nodes, _edges = _ts_call_graph.extract_call_edges(
                    file_path=file_path,
                    rel_path="OrdersService.cs",
                    language="csharp",
                    queries={
                        "calls": "(invocation_expression) @x",
                        "methods": "(method_declaration) @y",
                    },
                )
        # The file-caller sentinel id is deterministic from rel_path.
        sentinel_id = "symbol:csharp:OrdersService:<file>"
        self.assertIn(sentinel_id, nodes)
        props = nodes[sentinel_id]["props"]
        self.assertEqual(props.get("kind"), "file")
        self.assertEqual(props.get("language"), "csharp")

    def test_unresolved_callee_has_kind_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            file_path = Path(td) / "Caller.cs"
            file_path.write_text("void M() { External(); }\n", encoding="utf-8")
            with _patched_extract(
                definitions=["M"],
                call_names=["External"],
            ):
                nodes, edges = _ts_call_graph.extract_call_edges(
                    file_path=file_path,
                    rel_path="Caller.cs",
                    language="csharp",
                    queries={
                        "calls": "(invocation_expression) @x",
                        "methods": "(method_declaration) @y",
                    },
                )
        unresolved_id = "symbol:unresolved:External"
        self.assertIn(unresolved_id, nodes)
        props = nodes[unresolved_id]["props"]
        self.assertEqual(props.get("kind"), "unresolved")
        # ``resolved=False`` is the existing contract; the new kind must
        # not displace it.
        self.assertFalse(props.get("resolved", True))
        # The calls edge still points at the same id.
        self.assertTrue(
            any(e["to"] == unresolved_id for e in edges),
            "expected at least one calls edge to the unresolved sentinel",
        )

    def test_definition_symbol_keeps_existing_props(self) -> None:
        """Regression guard: definition nodes carry no ``kind`` here.

        ``extract_call_edges`` emits raw definition stubs without
        a ``kind`` (the canonical kind comes from
        ``_ts_definitions.promote_definition_symbols`` later in the
        strategy pipeline). Adding kinds to the *sentinels* must not
        leak a kind onto the *real* definition stubs.
        """
        with tempfile.TemporaryDirectory() as td:
            file_path = Path(td) / "Sample.cs"
            file_path.write_text("void Hello() {}\n", encoding="utf-8")
            with _patched_extract(
                definitions=["Hello"],
                call_names=[],
            ):
                nodes, _edges = _ts_call_graph.extract_call_edges(
                    file_path=file_path,
                    rel_path="Sample.cs",
                    language="csharp",
                    queries={
                        "calls": "(invocation_expression) @x",
                        "methods": "(method_declaration) @y",
                    },
                )
        def_id = "symbol:csharp:Sample:Hello"
        self.assertIn(def_id, nodes)
        # The raw definition stub has no kind; promotion to the
        # canonical vocabulary happens elsewhere.
        self.assertNotIn("kind", nodes[def_id]["props"])

    def test_non_csharp_language_also_gets_sentinel_kinds(self) -> None:
        """Sentinel kinds apply to every language, not just C#.

        The shared call-graph helper is language-agnostic so the kind
        stamping must be too. We pick Java as the canary because it
        also mints unresolved sentinels (no callgraph-origin override
        for Java currently). Java's
        :func:`_ts_call_graph._definition_query_names` returns
        ``("exports",)``, so the test driver supplies an ``exports``
        query.
        """
        with tempfile.TemporaryDirectory() as td:
            file_path = Path(td) / "App.java"
            file_path.write_text("void run() { go(); }\n", encoding="utf-8")
            with _patched_extract(
                definitions=["run"],
                call_names=["go"],
            ):
                nodes, _edges = _ts_call_graph.extract_call_edges(
                    file_path=file_path,
                    rel_path="App.java",
                    language="java",
                    queries={
                        "calls": "(method_invocation) @x",
                        "exports": "(method_declaration) @y",
                    },
                )
        java_sentinel = "symbol:java:App:<file>"
        self.assertIn(java_sentinel, nodes)
        self.assertEqual(nodes[java_sentinel]["props"].get("kind"), "file")
        self.assertEqual(
            nodes["symbol:unresolved:go"]["props"].get("kind"),
            "unresolved",
        )


if __name__ == "__main__":
    unittest.main()
