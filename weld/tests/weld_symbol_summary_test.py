"""bd p6ke: a symbol's own docstring is evidence too, one level below the
module (bd ph1g / ADR 0114).

ADR 0114 gave ``file:`` nodes ``props.summary`` from the module docstring.
Symbol nodes got none, even though the report that started this ((bd 9ucf /
ph1g) explicitly named a symbol as an expected match:
``symbol:py:weld.serializer:dumps_graph``. A query for a name stated only in
a function's own docstring -- nowhere in its signature -- still matched
nothing, the same defect one level down.

The read path needed no change: :mod:`weld.query_index` and
:mod:`weld._match_surface` already key on ``props.summary`` regardless of
node type (proven directly by :class:`SummaryIsIndexedForASymbolTest` below).
The gap was entirely on the write side -- :mod:`weld.strategies.python_callgraph`
minted symbol nodes without ever reading ``ast.get_docstring`` on the
``FunctionDef`` / ``AsyncFunctionDef`` / ``ClassDef`` it already had a parsed
tree for.

These tests pin the two halves of the fix:

* :func:`weld.strategies._python_anchor.symbol_summary` -- the same
  paragraph/collapse/bound contract :func:`module_summary` already
  implements, read one level down;
* :mod:`weld.strategies.python_callgraph` -- that it lands the result on
  ``props.summary`` for every defined symbol, always present.

See also :mod:`weld.tests.query_corpus` (entry ``bd p6ke``), which pins the
reported gap itself against the shared eval-corpus fixture graph, and
:mod:`weld.tests.weld_module_summary_test`, whose ``ModuleSummaryTest``
already exhaustively covers the shared paragraph/collapse/bound reduction --
not re-derived here.
"""

from __future__ import annotations

import ast
import tempfile
import textwrap
import unittest
from pathlib import Path

from weld.query_index import build_index, candidate_nodes, node_tokens
from weld.strategies import python_callgraph as pc
from weld.strategies._python_anchor import symbol_summary


def _function(src: str) -> ast.FunctionDef:
    return ast.parse(src).body[0]


def _class(src: str) -> ast.ClassDef:
    return ast.parse(src).body[0]


class SymbolSummaryTest(unittest.TestCase):
    """``symbol_summary`` reduces a def/class docstring the same way
    ``module_summary`` reduces a module's -- read one level down."""

    def test_function_docstring_first_paragraph_is_the_summary(self) -> None:
        node = _function(
            'def dumps_graph():\n'
            '    """Emit the canonical JSON text for ``graph``.\n\n'
            '    Applies canonical_graph then serialises.\n'
            '    """\n'
        )
        self.assertEqual(
            symbol_summary(node), "Emit the canonical JSON text for ``graph``."
        )

    def test_class_docstring_is_read_too(self) -> None:
        node = _class('class Graph:\n    """The in-memory graph."""\n')
        self.assertEqual(symbol_summary(node), "The in-memory graph.")

    def test_async_function_docstring_is_read_too(self) -> None:
        node = ast.parse(
            'async def fetch():\n    """Fetch the remote payload."""\n'
        ).body[0]
        self.assertEqual(symbol_summary(node), "Fetch the remote payload.")

    def test_a_symbol_without_a_docstring_summarises_to_nothing(self) -> None:
        node = _function("def helper():\n    return 1\n")
        self.assertEqual(symbol_summary(node), "")

    def test_a_wrapped_opening_paragraph_collapses_to_one_line(self) -> None:
        node = _function(
            'def f():\n'
            '    """Strategy: top-level classes and functions\n'
            '    from Python modules.\n"""\n'
        )
        self.assertEqual(
            symbol_summary(node),
            "Strategy: top-level classes and functions from Python modules.",
        )

    def test_the_bound_applies_to_symbols_too(self) -> None:
        """Same cap as ``module_summary`` (:data:`MAX_SUMMARY_LEN`), reused
        rather than re-derived -- a generated docstring on ONE of several
        thousand symbols must not put an unbounded string on the query hot
        path any more than a module's can."""
        from weld.strategies._python_anchor import MAX_SUMMARY_LEN

        long_doc = " ".join(["word"] * 400)
        node = _function(f'def f():\n    """{long_doc}"""\n')
        summary = symbol_summary(node)
        self.assertLessEqual(len(summary), MAX_SUMMARY_LEN)
        self.assertTrue(summary.startswith("word word"))


class PythonCallgraphSymbolSummaryPropTest(unittest.TestCase):
    """``python_callgraph.extract`` records the summary on the symbol node."""

    def _nodes(self, source_text: str) -> dict:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "fixture.py").write_text(
                textwrap.dedent(source_text), encoding="utf-8"
            )
            return pc.extract(root, {"glob": "*.py"}, {}).nodes

    def test_function_summary_lands_on_its_symbol_node(self) -> None:
        nodes = self._nodes(
            '''\
            def dumps_graph():
                """Emit the canonical JSON text for ``graph``."""
                return "{}"
            '''
        )
        node = nodes["symbol:py:fixture:dumps_graph"]
        self.assertEqual(
            node["props"]["summary"], "Emit the canonical JSON text for ``graph``."
        )

    def test_class_summary_lands_on_its_symbol_node(self) -> None:
        nodes = self._nodes(
            '''\
            class Graph:
                """The in-memory graph."""

                def load(self):
                    """Load the graph from disk."""
                    return None
            '''
        )
        self.assertEqual(
            nodes["symbol:py:fixture:Graph"]["props"]["summary"],
            "The in-memory graph.",
        )
        self.assertEqual(
            nodes["symbol:py:fixture:Graph.load"]["props"]["summary"],
            "Load the graph from disk.",
        )

    def test_the_key_is_present_even_when_empty(self) -> None:
        """Always-emitted, like ``kind`` and ``qualname`` beside it -- a prop
        that appears on some symbol nodes and not others is a prop every
        reader has to ``.get`` defensively."""
        nodes = self._nodes(
            '''\
            def undocumented():
                return None
            '''
        )
        node = nodes["symbol:py:fixture:undocumented"]
        self.assertEqual(node["props"]["summary"], "")

    def test_unresolved_sentinel_nodes_carry_no_summary_key(self) -> None:
        """Sentinel/stub nodes have no parsed body to read a docstring from
        -- out of scope, and must not gain a fabricated summary."""
        nodes = self._nodes(
            '''\
            def caller():
                nonexistent_function()
            '''
        )
        sentinel = nodes["symbol:unresolved:nonexistent_function"]
        self.assertNotIn("summary", sentinel["props"])


class SummaryIsIndexedForASymbolTest(unittest.TestCase):
    """The reported gap, reduced to its mechanism -- one level below the
    ``file:`` case :mod:`weld.tests.weld_module_summary_test` already pins.

    Nothing else about this node carries the docstring's wording: not its
    id, not its label, not its qualname. Before this fix that was true of
    every ``symbol:`` node in the real graph, so a query for a fact stated
    only in a function's own docstring fell through to whatever else the
    query could find (bd 9ucf's own report named this exact node).
    """

    def test_node_tokens_reads_a_symbol_summary(self) -> None:
        node = {
            "type": "symbol",
            "label": "dumps_graph",
            "props": {
                "file": "weld/serializer.py",
                "qualname": "dumps_graph",
                "summary": "Emit the canonical JSON text for ``graph``.",
            },
        }
        tokens = node_tokens("symbol:py:weld.serializer:dumps_graph", node)
        self.assertTrue(
            any("canonical" in token for token in tokens),
            f"the symbol's summary never reached the index: {tokens}",
        )

    def test_a_docstring_only_word_reaches_the_symbol_it_names(self) -> None:
        nodes = {
            "symbol:py:weld.serializer:dumps_graph": {
                "type": "symbol",
                "label": "dumps_graph",
                "props": {
                    "file": "weld/serializer.py",
                    "qualname": "dumps_graph",
                    "summary": "Emit the canonical JSON text for ``graph``.",
                },
            },
            "symbol:py:weld.serializer:canonical_graph": {
                "type": "symbol",
                "label": "canonical_graph",
                "props": {
                    "file": "weld/serializer.py",
                    "qualname": "canonical_graph",
                    "summary": "Return the canonical shape of graph.",
                },
            },
        }
        index = build_index(nodes)
        # "text" appears only in dumps_graph's own docstring -- not in its
        # id, label, qualname or file, and not in the sibling symbol's
        # summary either.
        self.assertEqual(
            candidate_nodes(index, ["text"]),
            {"symbol:py:weld.serializer:dumps_graph"},
        )


if __name__ == "__main__":
    unittest.main()
