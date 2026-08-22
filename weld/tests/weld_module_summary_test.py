"""bd ph1g: the module docstring's opening line is evidence, so index it.

``wd query "where is graph.json written"`` could not reach
``file:weld/serializer`` -- the documented single funnel every canonical
``graph.json`` emitter goes through -- because ZERO nodes in the live graph
carried the substring ``graph.json`` in any indexed field. The token could only
ever match nothing.

The fact was never missing from the *source*. ``weld/serializer.py`` opens::

    \"\"\"Canonical serializer for ``graph.json``.

Discovery parsed that file, kept its exports, constants, imports and line count,
and dropped the one sentence its author wrote to say what the module is. The
graph's only prose channel (``props.description``) belongs to an LLM enrichment
pass that had reached 1.96% of nodes, so "no node carries the token" was really
"weld reads 674 human-written summaries per run and stores none of them".

These tests pin the three halves of the fix as one contract:

* :func:`weld.strategies._python_anchor.module_summary` -- what counts as the
  summary (opening paragraph, collapsed, bounded);
* ``python_module`` -- that it lands on ``props.summary``, always present;
* :func:`weld.query_index.node_tokens` -- that the index reads it, which is what
  makes the token reachable on all three query backends at once (``node_tokens``
  is the single funnel ``bm25``, ``_sqlite_index`` and federation share).

The last test is the reported gap itself, reduced to its mechanism: a file node
whose only carrier of a dotted filename is its summary must be reachable by that
filename.
"""

from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

from weld.query_index import build_index, candidate_nodes, node_tokens
from weld.strategies._python_anchor import MAX_SUMMARY_LEN, module_summary
from weld.strategies.python_module import extract


def _tree(src: str) -> ast.Module:
    return ast.parse(src)


class ModuleSummaryTest(unittest.TestCase):
    """What the opening paragraph of a docstring reduces to."""

    def test_the_opening_line_is_the_summary(self) -> None:
        self.assertEqual(
            module_summary(_tree('"""Canonical serializer for ``graph.json``."""\n')),
            "Canonical serializer for ``graph.json``.",
        )

    def test_a_module_without_a_docstring_summarises_to_nothing(self) -> None:
        self.assertEqual(module_summary(_tree("x = 1\n")), "")

    def test_only_the_first_paragraph_survives(self) -> None:
        """The rest of a docstring is prose, and prose is not an index entry.

        weld's own docstrings run to sixty lines. Indexing all of it would put
        an essay in a channel ``candidate_nodes`` substring-scans once per query
        token, to describe a module that already said what it is in line one.
        """
        summary = module_summary(_tree(
            '"""Line one.\n\nA second paragraph that is not the summary.\n"""\n'
        ))
        self.assertEqual(summary, "Line one.")

    def test_a_wrapped_opening_paragraph_collapses_to_one_line(self) -> None:
        summary = module_summary(_tree(
            '"""Strategy: top-level classes and functions\n'
            '    from Python modules.\n"""\n'
        ))
        self.assertEqual(
            summary, "Strategy: top-level classes and functions from Python modules."
        )

    def test_a_whitespace_only_line_still_ends_the_paragraph(self) -> None:
        """``get_docstring`` dedents but does not strip trailing spaces.

        A separator line carrying the file's indentation is invisible to a
        reader and to ``str.split("\\n\\n")`` alike, so splitting on the literal
        blank line would swallow the whole docstring for any module whose
        author's editor did not trim trailing whitespace.
        """
        summary = module_summary(_tree(
            '"""First.\n   \n   Second paragraph.\n   """\n'
        ))
        self.assertEqual(summary, "First.")

    def test_an_oversized_summary_is_bounded(self) -> None:
        """A foreign repo's opening paragraph is not weld's to trust.

        In this repo the longest is 150 characters, so the cap never fires
        here -- it exists so a generated or pathological docstring cannot put an
        unbounded string on the query hot path.
        """
        long_doc = " ".join(["word"] * 400)
        summary = module_summary(_tree(f'"""{long_doc}"""\n'))
        self.assertLessEqual(len(summary), MAX_SUMMARY_LEN)
        self.assertTrue(summary.startswith("word word"))

    def test_the_bound_cuts_on_a_word_boundary(self) -> None:
        """A half-word is a token that matches nothing and misleads a reader."""
        summary = module_summary(_tree(f'"""{"alpha " * 200}"""\n'))
        self.assertNotIn("alph ", summary + " ")
        self.assertTrue(summary.endswith("alpha"))


class PythonModuleSummaryPropTest(unittest.TestCase):
    """``python_module.extract`` records the summary on the file node."""

    def _nodes(self, source_text: str) -> dict:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "fixture.py").write_text(source_text, encoding="utf-8")
            return extract(root, {"glob": "*.py", "package": ""}, {}).nodes

    def test_summary_lands_on_the_file_node(self) -> None:
        nodes = self._nodes(
            '"""Canonical serializer for ``graph.json``."""\n\n\ndef public():\n'
            "    return None\n"
        )
        node = next(iter(nodes.values()))
        self.assertEqual(
            node["props"]["summary"], "Canonical serializer for ``graph.json``."
        )

    def test_the_key_is_present_even_when_empty(self) -> None:
        """Always-emitted, like ``aliases`` and ``constants``.

        A prop that appears on some file nodes and not others is a prop every
        reader has to ``.get`` defensively, and a shape that differs run to run
        for reasons that are about the source rather than the schema.
        """
        nodes = self._nodes("def public():\n    return None\n")
        node = next(iter(nodes.values()))
        self.assertEqual(node["props"]["summary"], "")


class SummaryIsIndexedTest(unittest.TestCase):
    """The prop is only worth writing if the query path reads it."""

    def _node(self, summary: str) -> dict:
        return {
            "type": "file",
            "label": "serializer",
            "props": {"file": "weld/serializer.py", "summary": summary},
        }

    def test_node_tokens_reads_the_summary(self) -> None:
        tokens = node_tokens(
            "file:weld/serializer",
            self._node("Canonical serializer for ``graph.json``."),
        )
        self.assertTrue(
            any("graph.json" in token for token in tokens),
            f"the summary's tokens never reached the index: {tokens}",
        )

    def test_the_dotted_filename_reaches_the_node_it_names(self) -> None:
        """The reported gap, reduced to its mechanism (bd ph1g / 9ucf).

        Nothing else about this node carries ``graph.json``: not its id, not its
        label, not its path. Before the summary existed, that was true of every
        node in the real graph too, so the token matched nothing and the query
        fell through to whatever else it could find.
        """
        nodes = {
            "file:weld/serializer": self._node(
                "Canonical serializer for ``graph.json``."
            ),
            "file:weld/graph": {
                "type": "file",
                "label": "graph",
                "props": {"file": "weld/graph.py", "summary": "The in-memory graph."},
            },
        }
        index = build_index(nodes)
        self.assertEqual(
            candidate_nodes(index, ["graph.json"]), {"file:weld/serializer"}
        )

    def test_an_absent_summary_changes_nothing(self) -> None:
        """The prop is additive: a node without one indexes exactly as before."""
        props_only = {"type": "file", "label": "x", "props": {"file": "a/x.py"}}
        with_empty = {
            "type": "file", "label": "x", "props": {"file": "a/x.py", "summary": ""},
        }
        self.assertEqual(
            node_tokens("file:a/x", props_only), node_tokens("file:a/x", with_empty)
        )


if __name__ == "__main__":
    unittest.main()
