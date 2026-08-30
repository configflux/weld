"""bd cw4f (ADR 0125 follow-up): props.summary
for Java test-peer files, read from each file's own leading comment.

Split from weld_test_peer_file_summary_test.py (Go/Rust/TypeScript) for the
same reason weld_java_treesitter_test.py lives in the ADR 0069 ambient
(no-sandbox) lane rather than the hermetic one: no ``@pypi//tree_sitter_java``
target exists in this workspace (confirmed via ``bazel query`` -- the
package's wheel is not in ``requirements_lock.txt``), so this test parses via
whatever ``tree_sitter_java`` the ambient interpreter provides and self-skips
green under a sandbox that carries none, matching every other Java real-parse
target in ``weld/tests/treesitter_tests.bzl``. See
weld_test_peer_file_summary_test.py's module docstring for the full layer
breakdown this file's classes mirror.

The self-skip itself reuses the inline try/import/``skipTest`` guard
``weld_csharp_treesitter_test.py``'s real-grammar case
(``test_real_csharp_grammar_emits_disjoint_decl_buckets``) already
established for this exact "ambient grammar may be absent" situation --
factored here into one helper since every real-parse case in this module
needs it, rather than the single case that file has.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.query_index import build_index, candidate_nodes
from weld.strategies import test_peer as test_peer_strategy
from weld.strategies._ts_file_doc_comments import java_file_summary


def _skip_without_java_grammar(case: unittest.TestCase) -> None:
    try:
        import tree_sitter  # noqa: F401
        import tree_sitter_java  # noqa: F401
    except Exception:
        case.skipTest("tree_sitter / tree_sitter_java not available")


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class JavaFileSummaryTest(unittest.TestCase):
    def test_block_comment_before_package_is_summary(self) -> None:
        _skip_without_java_grammar(self)
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "ShapeTest.java"
            _write(
                f,
                "/*\n * Smoke test for shapes.\n *\n * Second para.\n */\n"
                "package com.example.shapes;\n\n"
                "public class ShapeTest {}\n",
            )
            self.assertEqual(java_file_summary(f), "Smoke test for shapes.")

    def test_javadoc_block_strips_star_prefixes(self) -> None:
        _skip_without_java_grammar(self)
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "ShapeTest.java"
            _write(
                f,
                "/**\n * Javadoc file comment.\n *\n * Second para.\n */\n"
                "package com.example.shapes;\n\n"
                "public class ShapeTest {}\n",
            )
            self.assertEqual(java_file_summary(f), "Javadoc file comment.")

    def test_header_survives_blank_line_before_package(self) -> None:
        """Unlike Go, Java has no ``go doc``-style zero-gap convention --
        the forward walk tolerates a blank line, matching TypeScript's own
        pin in weld_test_peer_file_summary_test.py."""
        _skip_without_java_grammar(self)
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "ShapeTest.java"
            _write(
                f,
                "// Line comment header.\n\npackage com.example.shapes;\n"
                "public class ShapeTest {}\n",
            )
            self.assertEqual(java_file_summary(f), "Line comment header.")

    def test_no_leading_comment_is_empty(self) -> None:
        _skip_without_java_grammar(self)
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "ShapeTest.java"
            _write(f, "package com.example.shapes;\npublic class ShapeTest {}\n")
            self.assertEqual(java_file_summary(f), "")


class TestPeerExtractSummaryTest(unittest.TestCase):
    """The real ``weld.strategies.test_peer.extract()`` entry point."""

    def test_java_summary_lands_on_test_peer_node(self) -> None:
        _skip_without_java_grammar(self)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root / "pkg/Foo.java", "package pkg;\npublic class Foo {}\n")
            _write(
                root / "pkg/FooTest.java",
                "/*\n * Tests Foo.\n */\npackage pkg;\n\npublic class FooTest {}\n",
            )
            result = test_peer_strategy.extract(root, {"glob": "pkg/*Test.java"}, {})
            node = result.nodes["file:pkg/FooTest"]
            self.assertEqual(node["props"]["summary"], "Tests Foo.")

    def test_repeated_extraction_is_identical(self) -> None:
        """Determinism: pure function of the parsed tree, like ADR 0118's
        Python equivalent (``symbol_summary``)."""
        _skip_without_java_grammar(self)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root / "pkg/FooTest.java",
                "/*\n * Tests Foo.\n */\npackage pkg;\n\npublic class FooTest {}\n",
            )
            source = {"glob": "pkg/*Test.java"}
            first = test_peer_strategy.extract(root, source, {}).nodes
            second = test_peer_strategy.extract(root, source, {}).nodes
            self.assertEqual(first, second)


class SummaryReachableViaQueryIndexTest(unittest.TestCase):
    """A name stated only in a Java test file's leading comment reaches
    that file's node through the unmodified generic read path -- isolated
    fixture, not the shared eval corpus (see the sibling module's
    docstring for why)."""

    def test_java_term_reaches_only_its_own_node(self) -> None:
        nodes = {
            "file:pkg/FooTest": {
                "type": "file",
                "label": "FooTest",
                "props": {
                    "file": "pkg/FooTest.java", "kind": "test", "language": "java",
                    "summary": "Exercises the crenellation widget end to end.",
                },
            },
            "file:pkg/BarTest": {
                "type": "file",
                "label": "BarTest",
                "props": {
                    "file": "pkg/BarTest.java", "kind": "test", "language": "java",
                    "summary": "Exercises the bar widget end to end.",
                },
            },
        }
        index = build_index(nodes)
        self.assertEqual(
            candidate_nodes(index, ["crenellation"]), {"file:pkg/FooTest"},
        )


if __name__ == "__main__":
    unittest.main()
