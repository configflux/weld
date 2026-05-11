"""Tests for ADR 0057 Wave 2 C++ header/source pairing edges.

Covers :mod:`weld.strategies._cpp_header_pairing` directly (unit tests
for the stem-match + one-cpp-in-dir fallback rules) and the end-to-end
wiring through :mod:`weld.strategies.tree_sitter` against the existing
``cpp_clang`` fixture (integration test).

Edge shape (ADR 0057 § Wave 2):

    file:<header>  --implemented_by-->  file:<source>

Confidence is ``definite`` for stem-match and ``inferred`` for the
one-cpp-in-dir fallback. Every emitted edge sets ``confidence``
explicitly per ADR 0050.
"""

from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)
_tests_dir = str(Path(__file__).resolve().parent)
if _tests_dir not in sys.path:
    sys.path.insert(0, _tests_dir)


def _entry(rel_path: str) -> dict:
    """Build the minimal per-file entry shape the pairing helper needs."""
    return {
        "rel_path": rel_path,
        "abs_path": Path(rel_path),
    }


class StemMatchPairingTest(unittest.TestCase):
    """``definite`` edges for same-dir / conventional-dir stem matches."""

    def test_same_directory_stem_match_collapses_to_self_edge(self) -> None:
        """A same-dir header/source pair shares a single ``file:`` node.

        ``weld._node_ids.file_id`` drops the extension, so
        ``file_id("lib_alpha/alpha.hpp") == file_id("lib_alpha/alpha.cpp")``.
        The pairing helper detects this and emits no edge -- the
        relationship is already represented by the single canonical
        file node for the stem. The interesting pairing cases live in
        the ``include/`` vs ``src/`` (or analogous) layouts.
        """
        from weld.strategies._cpp_header_pairing import (
            emit_header_source_pairs,
        )

        per_file = [
            _entry("lib_alpha/alpha.hpp"),
            _entry("lib_alpha/alpha.cpp"),
        ]
        edges: list[dict] = []
        appended = emit_header_source_pairs(per_file, edges)
        self.assertEqual(appended, 0)
        self.assertEqual(edges, [])

    def test_include_vs_src_stem_match_is_definite(self) -> None:
        from weld.strategies._cpp_header_pairing import (
            emit_header_source_pairs,
        )

        per_file = [
            _entry("include/foo.h"),
            _entry("src/foo.cpp"),
        ]
        edges: list[dict] = []
        appended = emit_header_source_pairs(per_file, edges)
        self.assertEqual(appended, 1)
        edge = edges[0]
        self.assertEqual(edge["from"], "file:include/foo")
        self.assertEqual(edge["to"], "file:src/foo")
        self.assertEqual(edge["type"], "implemented_by")
        self.assertEqual(edge["props"]["confidence"], "definite")
        self.assertEqual(edge["props"]["source_strategy"], "tree_sitter")

    def test_include_nested_path_stem_match(self) -> None:
        """``include/app/foo.h`` <-> ``src/app/foo.cpp``."""
        from weld.strategies._cpp_header_pairing import (
            emit_header_source_pairs,
        )

        per_file = [
            _entry("include/app/foo.h"),
            _entry("src/app/foo.cpp"),
        ]
        edges: list[dict] = []
        emit_header_source_pairs(per_file, edges)
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["props"]["confidence"], "definite")

    def test_module_local_include_layout(self) -> None:
        """``module/include/x.h`` <-> ``module/src/x.cpp``."""
        from weld.strategies._cpp_header_pairing import (
            emit_header_source_pairs,
        )

        per_file = [
            _entry("module/include/widget.h"),
            _entry("module/src/widget.cpp"),
        ]
        edges: list[dict] = []
        emit_header_source_pairs(per_file, edges)
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["props"]["confidence"], "definite")

    def test_inc_layout_stem_match(self) -> None:
        """``inc/foo.h`` -> ``src/foo.cpp`` (older convention)."""
        from weld.strategies._cpp_header_pairing import (
            emit_header_source_pairs,
        )

        per_file = [
            _entry("inc/foo.hpp"),
            _entry("src/foo.cc"),
        ]
        edges: list[dict] = []
        emit_header_source_pairs(per_file, edges)
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["props"]["confidence"], "definite")


class OneCppInDirFallbackTest(unittest.TestCase):
    """``inferred`` edges when no stem peer exists but one .cpp does."""

    def test_inferred_when_single_source_in_dir(self) -> None:
        from weld.strategies._cpp_header_pairing import (
            emit_header_source_pairs,
        )

        # ``api.h`` has no stem peer; only ``impl.cpp`` lives next to
        # it. The fallback emits ``api.h --implemented_by--> impl.cpp``
        # with ``inferred`` confidence.
        per_file = [
            _entry("dir/api.h"),
            _entry("dir/impl.cpp"),
        ]
        edges: list[dict] = []
        emit_header_source_pairs(per_file, edges)
        self.assertEqual(len(edges), 1)
        edge = edges[0]
        self.assertEqual(edge["from"], "file:dir/api")
        self.assertEqual(edge["to"], "file:dir/impl")
        self.assertEqual(edge["props"]["confidence"], "inferred")

    def test_no_edge_when_multiple_sources_in_dir(self) -> None:
        """Ambiguous: more than one .cpp -> no fallback edge."""
        from weld.strategies._cpp_header_pairing import (
            emit_header_source_pairs,
        )

        per_file = [
            _entry("dir/api.h"),
            _entry("dir/impl_a.cpp"),
            _entry("dir/impl_b.cpp"),
        ]
        edges: list[dict] = []
        emit_header_source_pairs(per_file, edges)
        self.assertEqual(edges, [])

    def test_no_edge_when_no_sources_in_dir(self) -> None:
        from weld.strategies._cpp_header_pairing import (
            emit_header_source_pairs,
        )

        per_file = [
            _entry("dir/api.h"),
        ]
        edges: list[dict] = []
        emit_header_source_pairs(per_file, edges)
        self.assertEqual(edges, [])

    def test_stem_match_wins_over_fallback(self) -> None:
        """A header with a stem peer never triggers the fallback."""
        from weld.strategies._cpp_header_pairing import (
            emit_header_source_pairs,
        )

        # ``api.h`` <-> ``api.cpp`` is a stem match (definite). The
        # fallback would have picked ``other.cpp`` -- but it must not.
        per_file = [
            _entry("include/api.h"),
            _entry("src/api.cpp"),
        ]
        edges: list[dict] = []
        emit_header_source_pairs(per_file, edges)
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["props"]["confidence"], "definite")
        self.assertEqual(edges[0]["to"], "file:src/api")


class HeaderPairingEdgeShapeTest(unittest.TestCase):
    """Every emitted edge satisfies ADR 0050's confidence-required rule."""

    def test_every_edge_carries_confidence(self) -> None:
        from weld.contract import CONFIDENCE_VALUES
        from weld.strategies._cpp_header_pairing import (
            emit_header_source_pairs,
        )

        per_file = [
            _entry("include/a.h"),
            _entry("src/a.cpp"),
            _entry("dir/api.h"),
            _entry("dir/only_impl.cpp"),
        ]
        edges: list[dict] = []
        emit_header_source_pairs(per_file, edges)
        self.assertGreater(len(edges), 0)
        for edge in edges:
            self.assertIn("props", edge)
            self.assertIn("confidence", edge["props"])
            self.assertIn(edge["props"]["confidence"], CONFIDENCE_VALUES)
            self.assertEqual(edge["type"], "implemented_by")

    def test_edge_type_in_valid_vocabulary(self) -> None:
        """ADR 0057 § Wave 2 adds ``implemented_by`` to VALID_EDGE_TYPES."""
        from weld.contract import VALID_EDGE_TYPES

        self.assertIn("implemented_by", VALID_EDGE_TYPES)

    def test_deterministic_order(self) -> None:
        """Edges appear sorted by header rel_path for reproducibility."""
        from weld.strategies._cpp_header_pairing import (
            emit_header_source_pairs,
        )

        per_file = [
            _entry("src/z.cpp"),
            _entry("include/z.h"),
            _entry("src/a.cpp"),
            _entry("include/a.h"),
            _entry("include/m.h"),
            _entry("src/m.cpp"),
        ]
        edges: list[dict] = []
        emit_header_source_pairs(per_file, edges)
        froms = [e["from"] for e in edges]
        self.assertEqual(froms, sorted(froms))

    def test_no_self_edge(self) -> None:
        """Same-directory same-stem headers and sources should not collapse to a self edge.

        ``file_id`` is path-without-extension so
        ``file_id("lib_alpha/alpha.hpp") == file_id("lib_alpha/alpha.cpp")``;
        the helper detects this and refuses the self-edge. The pair
        survives at the graph-id level only when header and source live
        in different directories (the normal ``include/`` vs ``src/``
        case).
        """
        from weld.strategies._cpp_header_pairing import (
            emit_header_source_pairs,
        )

        per_file = [
            _entry("lib_alpha/alpha.hpp"),
            _entry("lib_alpha/alpha.cpp"),
        ]
        edges: list[dict] = []
        emit_header_source_pairs(per_file, edges)
        for edge in edges:
            self.assertNotEqual(edge["from"], edge["to"])


class HeaderPairingIntegrationTest(unittest.TestCase):
    """End-to-end pairing against the ``cpp_clang`` fixture."""

    FIXTURE = Path(__file__).resolve().parent / "fixtures" / "cpp_clang"

    def _run(self):
        from cpp_resolver_fakes import fake_call_edges, fake_parse  # noqa: E402

        from weld.strategies import tree_sitter

        with mock.patch.object(
            tree_sitter, "TREE_SITTER_AVAILABLE", True,
        ), mock.patch.object(
            tree_sitter, "_parse_file_symbols", side_effect=fake_parse,
        ), mock.patch.object(
            tree_sitter, "_extract_call_edges", side_effect=fake_call_edges,
        ):
            return tree_sitter.extract(
                root=self.FIXTURE,
                source={
                    "glob": "**/*.cpp",
                    "language": "cpp",
                    "emit_calls": True,
                },
                context={},
            )

    def test_foo_h_pairs_with_foo_cpp(self) -> None:
        """include/foo.h --implemented_by--> src/foo.cpp (definite)."""
        result = self._run()
        pair_edges = [
            e for e in result.edges if e["type"] == "implemented_by"
        ]
        self.assertTrue(pair_edges, "expected at least one pair edge")
        froms = {e["from"] for e in pair_edges}
        # include/foo.h pairs with src/foo.cpp.
        self.assertIn("file:include/foo", froms)
        foo_edge = next(e for e in pair_edges if e["from"] == "file:include/foo")
        self.assertEqual(foo_edge["to"], "file:src/foo")
        self.assertEqual(foo_edge["props"]["confidence"], "definite")

    def test_app_h_pairs_with_app_cpp(self) -> None:
        """include/app.h --implemented_by--> src/app.cpp (definite)."""
        result = self._run()
        pair_edges = [
            e for e in result.edges if e["type"] == "implemented_by"
        ]
        froms = {e["from"] for e in pair_edges}
        self.assertIn("file:include/app", froms)
        app_edge = next(e for e in pair_edges if e["from"] == "file:include/app")
        self.assertEqual(app_edge["to"], "file:src/app")
        self.assertEqual(app_edge["props"]["confidence"], "definite")

    def test_every_pair_edge_carries_confidence(self) -> None:
        from weld.contract import CONFIDENCE_VALUES

        result = self._run()
        pair_edges = [
            e for e in result.edges if e["type"] == "implemented_by"
        ]
        self.assertTrue(pair_edges)
        for edge in pair_edges:
            self.assertIn(
                edge["props"]["confidence"], CONFIDENCE_VALUES,
                f"edge {edge} missing valid confidence",
            )


class HeaderPairingLanguageGatingTest(unittest.TestCase):
    """Pairing only runs for cpp + emit_calls. Python is unaffected."""

    def test_python_extraction_emits_no_pair_edges(self) -> None:
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pkg").mkdir()
            (root / "pkg" / "a.py").write_text(
                textwrap.dedent("""\
                    def f():
                        pass
                """),
            )
            with mock.patch.object(
                tree_sitter, "TREE_SITTER_AVAILABLE", True,
            ), mock.patch.object(
                tree_sitter, "_parse_file_symbols",
                return_value={
                    "exports": ["f"],
                    "classes": [],
                    "imports": [],
                },
            ):
                result = tree_sitter.extract(
                    root=root,
                    source={"glob": "**/*.py", "language": "python"},
                    context={},
                )
            pair_edges = [
                e for e in result.edges if e["type"] == "implemented_by"
            ]
            self.assertEqual(pair_edges, [])


if __name__ == "__main__":
    unittest.main()
