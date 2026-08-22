"""bd 5038-009x (ADR 0118 follow-up): doc-comment summaries end to end.

Companion to :mod:`weld.tests.weld_ts_doc_comments_test`, which pins the
backward-walk association logic directly; this file covers the layers above
it (split to keep each file under the line-count cap):

* :class:`ExtractDefinitionSummariesTest` -- the module's public entry
  point, :func:`~weld.strategies._ts_doc_comments.extract_definition_summaries`.
* :class:`TreeSitterExtractSummaryIntegrationTest` -- the real strategy
  entry point, :func:`weld.strategies.tree_sitter.extract`, proving the
  summary actually lands on ``props.summary`` of the minted ``symbol:``
  node (and does NOT appear on sentinel/synthetic nodes or on a deferred
  language's nodes, mirroring ADR 0118's
  ``test_unresolved_sentinel_nodes_carry_no_summary_key``).
* :class:`SummaryReachableViaQueryIndexTest` -- a name stated ONLY in a doc
  comment reaches its symbol through the unmodified generic read path,
  proving the ADR 0118 read-side claim ("nothing in the read path is
  Python-specific") a second time on a different write side. Uses its own
  small, isolated fixture rather than the shared eval corpus in
  :mod:`weld.tests.query_corpus` -- ADR 0114 already recorded that adding a
  node there moved cross-backend ranking statistics enough to flip a pinned
  pair, so this deliberately does not touch that file.
"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from weld.query_index import build_index, candidate_nodes, node_tokens
from weld.strategies import tree_sitter as ts_strategy
from weld.strategies._ts_doc_comments import (
    extract_definition_summaries,
    supports_doc_summaries,
)
from weld.strategies.tree_sitter import load_language_queries


class ExtractDefinitionSummariesTest(unittest.TestCase):
    """``extract_definition_summaries`` -- the module's public entry point."""

    def test_unsupported_language_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "x.ts"
            f.write_text("export function x() {}\n", encoding="utf-8")
            queries = load_language_queries("typescript")
            self.assertIsNone(extract_definition_summaries(f, "typescript", queries))

    def test_supports_doc_summaries_matches_registered_languages(self) -> None:
        self.assertTrue(supports_doc_summaries("go"))
        self.assertTrue(supports_doc_summaries("rust"))
        self.assertFalse(supports_doc_summaries("typescript"))
        self.assertFalse(supports_doc_summaries("java"))

    def test_go_definition_summaries_by_name(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "geometry.go"
            f.write_text(
                textwrap.dedent(
                    """\
                    package geometry

                    // Circle is a shape defined by its radius.
                    type Circle struct {
                        Radius float64
                    }

                    func Undocumented() int { return 1 }
                    """
                ),
                encoding="utf-8",
            )
            queries = load_language_queries("go")
            summaries = extract_definition_summaries(f, "go", queries)
            self.assertEqual(
                summaries["Circle"], "Circle is a shape defined by its radius.",
            )
            self.assertEqual(summaries["Undocumented"], "")

    def test_rust_definition_summaries_by_name(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "lib.rs"
            f.write_text(
                textwrap.dedent(
                    """\
                    /// Axis-aligned rectangle.
                    pub struct Rectangle {
                        pub width: f64,
                    }

                    pub fn undocumented() -> i32 { 1 }
                    """
                ),
                encoding="utf-8",
            )
            queries = load_language_queries("rust")
            summaries = extract_definition_summaries(f, "rust", queries)
            self.assertEqual(summaries["Rectangle"], "Axis-aligned rectangle.")
            self.assertEqual(summaries["undocumented"], "")


class TreeSitterExtractSummaryIntegrationTest(unittest.TestCase):
    """The real strategy entry point wires ``props.summary`` end to end."""

    def test_go_symbol_summary_lands_on_its_node(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "geometry.go").write_text(
                textwrap.dedent(
                    """\
                    package geometry

                    // Circle is a shape defined by its radius.
                    type Circle struct {
                        Radius float64
                    }
                    """
                ),
                encoding="utf-8",
            )
            result = ts_strategy.extract(
                root, {"glob": "*.go", "language": "go", "id_prefix": "go"}, {},
            )
            node = result.nodes["symbol:go:geometry:Circle"]
            self.assertEqual(
                node["props"]["summary"], "Circle is a shape defined by its radius.",
            )

    def test_rust_symbol_summary_lands_on_its_node(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "lib.rs").write_text(
                "/// Axis-aligned rectangle.\npub struct Rectangle { pub width: f64 }\n",
                encoding="utf-8",
            )
            result = ts_strategy.extract(
                root, {"glob": "*.rs", "language": "rust", "id_prefix": "rust"}, {},
            )
            node = result.nodes["symbol:rust:lib:Rectangle"]
            self.assertEqual(node["props"]["summary"], "Axis-aligned rectangle.")

    def test_the_key_is_present_even_when_empty(self) -> None:
        """Mirrors ADR 0118's Python contract: present, empty, not absent."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "geometry.go").write_text(
                "package geometry\n\nfunc Undocumented() int { return 1 }\n",
                encoding="utf-8",
            )
            result = ts_strategy.extract(
                root, {"glob": "*.go", "language": "go", "id_prefix": "go"}, {},
            )
            node = result.nodes["symbol:go:geometry:Undocumented"]
            self.assertEqual(node["props"]["summary"], "")

    def test_unresolved_sentinel_and_file_caller_nodes_carry_no_summary_key(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "geometry.go").write_text(
                textwrap.dedent(
                    """\
                    package geometry

                    // Area does the math.
                    func Area() float64 {
                        return helper()
                    }
                    """
                ),
                encoding="utf-8",
            )
            result = ts_strategy.extract(
                root,
                {"glob": "*.go", "language": "go", "id_prefix": "go", "emit_calls": True},
                {},
            )
            self.assertNotIn(
                "summary", result.nodes["symbol:unresolved:helper"]["props"],
            )
            self.assertNotIn(
                "summary", result.nodes["symbol:go:geometry:<file>"]["props"],
            )

    def test_deferred_language_gets_no_summary_key_at_all(self) -> None:
        """TypeScript is deferred (see weld_ts_doc_comments_test module
        docstring); unlike Go/Rust, its symbols must not gain even an empty
        ``summary`` key -- that would misrepresent "never looked" as
        "looked, found nothing"."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "x.ts").write_text(
                "/** Doc comment. */\nexport function x() {}\n", encoding="utf-8",
            )
            result = ts_strategy.extract(
                root, {"glob": "*.ts", "language": "typescript", "id_prefix": "ts"}, {},
            )
            node = result.nodes["symbol:typescript:x:x"]
            self.assertNotIn("summary", node["props"])

    def test_repeated_extraction_is_identical(self) -> None:
        """Determinism: pure function of the parsed tree, like ADR 0118's
        Python equivalent (``symbol_summary``)."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "geometry.go").write_text(
                "package geometry\n\n// Doc.\nfunc X() {}\n", encoding="utf-8",
            )
            source = {"glob": "*.go", "language": "go", "id_prefix": "go"}
            first = ts_strategy.extract(root, source, {}).nodes
            second = ts_strategy.extract(root, source, {}).nodes
            self.assertEqual(first, second)


class SummaryReachableViaQueryIndexTest(unittest.TestCase):
    """A name stated only in a Go/Rust doc comment reaches its symbol
    through the unmodified generic read path -- ADR 0118's read-side claim,
    proven again on a different write side. Isolated fixture, not the
    shared eval corpus (see module docstring)."""

    def test_go_doc_comment_only_term_is_indexed(self) -> None:
        node = {
            "type": "symbol",
            "label": "Circle",
            "props": {
                "file": "geometry.go",
                "qualname": "Circle",
                "language": "go",
                "summary": "A shape carrying a wobblesnort radius.",
            },
        }
        tokens = node_tokens("symbol:go:geometry:Circle", node)
        self.assertTrue(any("wobblesnort" in t for t in tokens))

    def test_go_doc_comment_only_term_reaches_only_its_own_symbol(self) -> None:
        nodes = {
            "symbol:go:geometry:Circle": {
                "type": "symbol",
                "label": "Circle",
                "props": {
                    "file": "geometry.go", "qualname": "Circle", "language": "go",
                    "summary": "A shape carrying a wobblesnort radius.",
                },
            },
            "symbol:go:geometry:Rectangle": {
                "type": "symbol",
                "label": "Rectangle",
                "props": {
                    "file": "geometry.go", "qualname": "Rectangle", "language": "go",
                    "summary": "An axis-aligned box.",
                },
            },
        }
        index = build_index(nodes)
        self.assertEqual(
            candidate_nodes(index, ["wobblesnort"]),
            {"symbol:go:geometry:Circle"},
        )

    def test_rust_doc_comment_only_term_reaches_only_its_own_symbol(self) -> None:
        nodes = {
            "symbol:rust:src.geometry:describe": {
                "type": "symbol",
                "label": "describe",
                "props": {
                    "file": "src/geometry.rs", "qualname": "describe", "language": "rust",
                    "summary": "Uses zorbulator-based dynamic dispatch.",
                },
            },
            "symbol:rust:src.geometry:area": {
                "type": "symbol",
                "label": "area",
                "props": {
                    "file": "src/geometry.rs", "qualname": "area", "language": "rust",
                    "summary": "Computes the shape's area.",
                },
            },
        }
        index = build_index(nodes)
        self.assertEqual(
            candidate_nodes(index, ["zorbulator"]),
            {"symbol:rust:src.geometry:describe"},
        )


if __name__ == "__main__":
    unittest.main()
