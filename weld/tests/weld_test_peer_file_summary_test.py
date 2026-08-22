"""bd cw4f (ADR 0125 follow-up): props.summary
for Go/Rust/TypeScript test-peer files, read from each file's own leading
comment.

Companion to weld_test_peer_java_file_summary_test.py, which covers the same
contract for Java in the ADR 0069 ambient (no-sandbox) lane -- no
``@pypi//tree_sitter_java`` target exists (confirmed via ``bazel query``),
unlike Go/Rust/TypeScript, whose grammars are pinned in
``requirements_lock.txt``.

Layers, mirroring ``weld_ts_doc_comments_test.py`` /
``weld_ts_doc_comments_integration_test.py``'s split for the *symbol*-level
reader:

* Per-language unit tests on ``weld.strategies._ts_file_doc_comments``'s
  public entry points -- the reviewable risk here is comment-to-*file*
  association (not comment-to-symbol): Go's zero-gap adjacency to
  ``package`` (real ``go doc`` behavior, verified by probe -- see that
  module's docstring), TypeScript's tolerance of a blank line before the
  first import (a real bug an earlier version of that module had, caught
  against the pinned tier1 TypeScript fixture and pinned here so it does not
  regress), and Rust's inner-vs-outer doc-marker distinction.
* :class:`TestPeerExtractSummaryTest` -- the real
  ``weld.strategies.test_peer.extract()`` entry point, proving
  ``props.summary`` lands on the actual test-peer file node, not just the
  low-level per-language function.
* :class:`SummaryReachableViaQueryIndexTest` -- a term stated ONLY in a test
  file's leading comment reaches that file's node through the unmodified
  generic read path, mirroring ``weld_ts_doc_comments_test``'s own
  ``SummaryReachableViaQueryIndexTest``. Uses its own small, isolated
  fixture rather than the shared eval corpus in ``weld.tests.query_corpus``
  -- ADR 0124's own precedent for avoiding that corpus's cross-backend
  ranking blast radius.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.query_index import build_index, candidate_nodes
from weld.strategies import test_peer as test_peer_strategy
from weld.strategies._ts_file_doc_comments import (
    go_file_summary,
    rust_file_summary,
    typescript_file_summary,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class GoFileSummaryTest(unittest.TestCase):
    def test_leading_comment_before_package_is_summary(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "geometry_test.go"
            _write(
                f,
                "// Tests the geometry package.\n// Second line.\n"
                "package geometry\n\nfunc TestX(t *testing.T) {}\n",
            )
            self.assertEqual(
                go_file_summary(f), "Tests the geometry package. Second line.",
            )

    def test_blank_line_before_package_rejects_detached_header(self) -> None:
        """``go doc``'s own convention: a package doc comment must be
        immediately adjacent to ``package``, no blank line -- verified
        against the real grammar (see ``_ts_file_doc_comments``' module
        docstring), not assumed."""
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "x_test.go"
            _write(f, "// Unrelated header.\n\npackage p\n")
            self.assertEqual(go_file_summary(f), "")

    def test_no_leading_comment_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "x_test.go"
            _write(f, "package p\n\nfunc TestX() {}\n")
            self.assertEqual(go_file_summary(f), "")


class RustFileSummaryTest(unittest.TestCase):
    def test_inner_doc_run_survives_blank_line_before_use(self) -> None:
        """Unlike Go, ``//!`` does not need to touch the next item --
        verified against this repo's own ``tests/geometry.rs`` fixture,
        which has exactly this shape."""
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "geometry.rs"
            _write(
                f,
                "//! Integration test doc.\n//!\n//! Second para.\n\n"
                "use sample::Circle;\n\n#[test]\nfn x() {}\n",
            )
            self.assertEqual(rust_file_summary(f), "Integration test doc.")

    def test_plain_line_comment_is_not_doc_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "x.rs"
            _write(f, "// plain, not inner doc\nuse foo::Bar;\n")
            self.assertEqual(rust_file_summary(f), "")

    def test_outer_doc_marker_is_not_file_level(self) -> None:
        """``///`` documents the FOLLOWING item, not the file -- must not
        leak into the file-level reader."""
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "x.rs"
            _write(f, "/// Doc for the next struct, not the file.\npub struct X {}\n")
            self.assertEqual(rust_file_summary(f), "")


class TypeScriptFileSummaryTest(unittest.TestCase):
    def test_header_survives_blank_line_before_import(self) -> None:
        """Regression pin: an earlier version of this reader used Go's
        zero-gap backward walk for TypeScript too and returned "" here -- a
        blank line between a header comment and the first import is
        idiomatic TS/JS style, confirmed against the pinned tier1
        TypeScript fixture."""
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "geometry.test.ts"
            _write(
                f,
                "// Vitest suite doc.\n// Second line.\n\n"
                'import { describe, it } from "vitest";\n',
            )
            self.assertEqual(
                typescript_file_summary(f), "Vitest suite doc. Second line.",
            )

    def test_second_detached_block_is_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "x.test.ts"
            _write(
                f,
                '// Block A.\n\n// Block B (excluded).\nimport { x } from "y";\n',
            )
            self.assertEqual(typescript_file_summary(f), "Block A.")

    def test_jsdoc_block_strips_star_prefixes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "x.test.ts"
            _write(
                f,
                "/**\n * JSDoc file comment.\n *\n * Second para.\n */\n"
                'import { x } from "y";\n',
            )
            self.assertEqual(typescript_file_summary(f), "JSDoc file comment.")

    def test_no_leading_comment_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "x.test.ts"
            _write(f, 'import { x } from "y";\n')
            self.assertEqual(typescript_file_summary(f), "")


class TestPeerExtractSummaryTest(unittest.TestCase):
    """The real ``weld.strategies.test_peer.extract()`` entry point."""

    def test_go_summary_lands_on_test_peer_node(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root / "pkg/foo_test.go",
                "// Tests foo.\npackage pkg\n\nfunc TestFoo(t *testing.T) {}\n",
            )
            result = test_peer_strategy.extract(root, {"glob": "**/*_test.go"}, {})
            node = result.nodes["file:pkg/foo_test"]
            self.assertEqual(node["props"]["summary"], "Tests foo.")

    def test_rust_summary_lands_on_test_peer_node(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root / "src/lib.rs", "pub struct C;\n")
            _write(
                root / "tests/lib.rs",
                "//! Crate integration test.\n\nuse sample::C;\n",
            )
            result = test_peer_strategy.extract(root, {"glob": "tests/*.rs"}, {})
            node = result.nodes["file:tests/lib"]
            self.assertEqual(node["props"]["summary"], "Crate integration test.")

    def test_typescript_summary_lands_on_test_peer_node(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root / "src/foo.ts", "export const x = 1;\n")
            _write(
                root / "src/foo.test.ts",
                '// Vitest suite for foo.\nimport { x } from "./foo";\n',
            )
            result = test_peer_strategy.extract(root, {"glob": "src/*.test.ts"}, {})
            node = result.nodes["file:src/foo.test"]
            self.assertEqual(node["props"]["summary"], "Vitest suite for foo.")

    def test_repeated_extraction_is_identical(self) -> None:
        """Determinism: pure function of the parsed tree, like ADR 0118's
        Python equivalent (``symbol_summary``)."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root / "pkg/foo_test.go",
                "// Tests foo.\npackage pkg\n\nfunc TestFoo(t *testing.T) {}\n",
            )
            source = {"glob": "**/*_test.go"}
            first = test_peer_strategy.extract(root, source, {}).nodes
            second = test_peer_strategy.extract(root, source, {}).nodes
            self.assertEqual(first, second)


class SummaryReachableViaQueryIndexTest(unittest.TestCase):
    """A name stated only in a test file's leading comment reaches that
    file's node through the unmodified generic read path -- isolated
    fixture, not the shared eval corpus (see module docstring)."""

    def _reaches_only_its_own_node(self, language: str, term: str) -> None:
        nodes = {
            "file:pkg/foo_test": {
                "type": "file",
                "label": "foo_test",
                "props": {
                    "file": "pkg/foo_test.ext", "kind": "test", "language": language,
                    "summary": f"Exercises the {term} widget end to end.",
                },
            },
            "file:pkg/bar_test": {
                "type": "file",
                "label": "bar_test",
                "props": {
                    "file": "pkg/bar_test.ext", "kind": "test", "language": language,
                    "summary": "Exercises the bar widget end to end.",
                },
            },
        }
        index = build_index(nodes)
        self.assertEqual(candidate_nodes(index, [term]), {"file:pkg/foo_test"})

    def test_go_term_reaches_only_its_own_node(self) -> None:
        self._reaches_only_its_own_node("go", "wobblesnort")

    def test_rust_term_reaches_only_its_own_node(self) -> None:
        self._reaches_only_its_own_node("rust", "zorbulator")

    def test_typescript_term_reaches_only_its_own_node(self) -> None:
        self._reaches_only_its_own_node("typescript", "flimflammer")


if __name__ == "__main__":
    unittest.main()
