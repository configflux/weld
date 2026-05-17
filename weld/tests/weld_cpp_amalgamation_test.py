"""Tests for ADR 0062: C++ amalgamation file rank boost.

Two surfaces under test:

1. Discovery side (``weld.strategies._cpp_tree_sitter``):
   ``props.amalgamation = True`` is stamped on file nodes whose path
   matches the well-known amalgamation conventions.
2. Ranker side (``weld.ranking``): on a single-token navigation query,
   amalgamation file nodes outrank same-score modular peers; the boost
   does NOT promote symbol nodes that happen to share the marker, does
   NOT fire on multi-token queries, and does NOT change non-cpp
   ranking.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.bm25 import BM25Corpus  # noqa: E402
from weld.graph import Graph  # noqa: E402
from weld.ranking import (  # noqa: E402
    is_amalgamation_file_node,
    rank_query_matches,
)


def _stamp(rel_path: str) -> dict:
    from weld.strategies import _cpp_tree_sitter

    node_props: dict = {"file": rel_path}
    _cpp_tree_sitter.enrich_file_node(
        nodes={},
        edges=[],
        file_node_id=f"file:{rel_path.removesuffix('.hpp')}",
        node_props=node_props,
        symbols={"exports": ["basic_json"], "classes": ["basic_json"]},
        source_text="namespace nlohmann { class basic_json {}; }",
        source_strategy="tree_sitter",
    )
    return node_props


class AmalgamationMarkerStampingTest(unittest.TestCase):
    """``_cpp_tree_sitter.enrich_file_node`` stamps ``amalgamation``."""

    def test_single_include_path_gets_marker(self) -> None:
        self.assertTrue(_stamp("single_include/nlohmann/json.hpp").get("amalgamation"))

    def test_dist_single_header_path_gets_marker(self) -> None:
        self.assertTrue(_stamp("dist/single_header/foo.hpp").get("amalgamation"))

    def test_amalgamated_dir_gets_marker(self) -> None:
        self.assertTrue(_stamp("amalgamated/lib.hpp").get("amalgamation"))

    def test_amalgamation_basename_gets_marker(self) -> None:
        self.assertTrue(_stamp("vendor/sqlite3.amalgamation.c").get("amalgamation"))

    def test_modular_path_does_not_get_marker(self) -> None:
        # Absent OR explicitly False is fine; the rank gate only fires
        # on truthy.
        self.assertFalse(_stamp("include/nlohmann/json.hpp").get("amalgamation", False))

    def test_arbitrary_dir_does_not_get_marker(self) -> None:
        self.assertFalse(_stamp("src/foo/bar.cpp").get("amalgamation", False))

    def test_unrelated_dist_path_does_not_get_marker(self) -> None:
        # ``dist/`` alone is not enough -- needs to look like a
        # single-header bundle.
        self.assertFalse(_stamp("dist/library.hpp").get("amalgamation", False))


class AmalgamationFileNodeDetectorTest(unittest.TestCase):
    """``is_amalgamation_file_node`` only flags type=file with the marker."""

    def test_file_node_with_marker_returns_true(self) -> None:
        self.assertTrue(is_amalgamation_file_node({
            "id": "file:single_include/nlohmann/json",
            "type": "file",
            "props": {"amalgamation": True, "language": "cpp"},
        }))

    def test_symbol_node_with_marker_returns_false(self) -> None:
        # Even if a downstream pass mistakenly stamps the marker on a
        # symbol node, the rank gate must only act on file-typed nodes.
        self.assertFalse(is_amalgamation_file_node({
            "id": "symbol:cpp:nlohmann.json:basic_json",
            "type": "symbol",
            "props": {"amalgamation": True},
        }))

    def test_file_node_without_marker_returns_false(self) -> None:
        self.assertFalse(is_amalgamation_file_node({
            "id": "file:include/nlohmann/json",
            "type": "file",
            "props": {"language": "cpp"},
        }))


def _two_file_nodes() -> dict:
    """Two file nodes with identical exports; one has the amalg marker."""
    amalg_id = "file:single_include/nlohmann/json"
    modular_id = "file:include/nlohmann/json"
    exports = ["basic_json"] * 50
    return {
        amalg_id: {
            "type": "file",
            "label": "json",
            "props": {
                "file": "single_include/nlohmann/json.hpp",
                "exports": exports,
                "language": "cpp",
                "source_strategy": "tree_sitter",
                "authority": "derived",
                "confidence": "definite",
                "amalgamation": True,
            },
        },
        modular_id: {
            "type": "file",
            "label": "json",
            "props": {
                "file": "include/nlohmann/json.hpp",
                "exports": exports,
                "language": "cpp",
                "source_strategy": "tree_sitter",
                "authority": "derived",
                "confidence": "definite",
            },
        },
    }


class AmalgamationBoostTiebreakTest(unittest.TestCase):
    """Amalgamation file beats same-score modular peer on single-token query."""

    def test_amalgamation_file_outranks_modular_peer(self) -> None:
        nodes = _two_file_nodes()
        ranked = rank_query_matches(
            list(nodes.items()), [["basic_json"]],
            BM25Corpus.from_nodes(nodes),
            structural_scores={k: 0.0 for k in nodes},
        )
        self.assertEqual(ranked[0][0], "file:single_include/nlohmann/json")
        self.assertEqual(ranked[1][0], "file:include/nlohmann/json")

    def test_multi_token_query_does_not_apply_boost(self) -> None:
        # Boost only fires for single-token navigation queries. On a
        # multi-token query the existing tiebreak chain (id) wins, so
        # the modular peer (lex-smaller id) sorts ahead.
        nodes = _two_file_nodes()
        ranked = rank_query_matches(
            list(nodes.items()), [["basic_json"], ["nlohmann"]],
            BM25Corpus.from_nodes(nodes),
            structural_scores={k: 0.0 for k in nodes},
        )
        self.assertEqual(ranked[0][0], "file:include/nlohmann/json")

    def test_boost_does_not_promote_unresolved_sentinel(self) -> None:
        # Resolution penalty stays primary -- an unresolved sentinel
        # never beats a resolved file even with the marker.
        nodes = {
            "symbol:unresolved:basic_json": {
                "type": "symbol", "label": "basic_json",
                "props": {
                    "qualname": "basic_json",
                    "resolved": False,
                    "confidence": "speculative",
                },
            },
            "file:single_include/nlohmann/json": {
                "type": "file", "label": "json",
                "props": {
                    "file": "single_include/nlohmann/json.hpp",
                    "exports": ["basic_json"],
                    "amalgamation": True,
                    "authority": "derived",
                    "confidence": "definite",
                },
            },
        }
        ranked = rank_query_matches(
            list(nodes.items()), [["basic_json"]],
            BM25Corpus.from_nodes(nodes),
            structural_scores={k: 0.0 for k in nodes},
        )
        self.assertEqual(ranked[0][0], "file:single_include/nlohmann/json")


class SyntheticAmalgamationGraphQueryTest(unittest.TestCase):
    """End-to-end: synthetic 24K-char amalgamation outranks modular peer."""

    def _make_graph_with_amalgamation_pair(self) -> Graph:
        # 24K+ char body to mirror the real file shape -- BM25 sees
        # the export tokens, so we feed it a long export list.
        amalgamation_body_size = max(24_000, len("basic_json ") * 2200)
        many_exports = ["basic_json"] * (amalgamation_body_size // 11)
        nodes = {
            "file:single_include/nlohmann/json": {
                "type": "file", "label": "json",
                "props": {
                    "file": "single_include/nlohmann/json.hpp",
                    "exports": many_exports,
                    "line_count": 24_765,
                    "language": "cpp",
                    "source_strategy": "tree_sitter",
                    "authority": "derived",
                    "confidence": "definite",
                    "roles": ["implementation"],
                    "amalgamation": True,
                },
            },
            "file:include/nlohmann/json": {
                "type": "file", "label": "json",
                "props": {
                    "file": "include/nlohmann/json.hpp",
                    "exports": ["basic_json"],
                    "types": ["basic_json"],
                    "line_count": 226,
                    "language": "cpp",
                    "source_strategy": "tree_sitter",
                    "authority": "derived",
                    "confidence": "definite",
                    "roles": ["implementation"],
                },
            },
        }
        tmp = tempfile.mkdtemp()
        g = Graph(Path(tmp))
        g._data = {
            "meta": {
                "version": 1,
                "updated_at": "2026-05-14T00:00:00+00:00",
            },
            "nodes": nodes, "edges": [],
        }
        return g

    def test_amalgamation_file_in_top_two(self) -> None:
        g = self._make_graph_with_amalgamation_pair()
        result = g.query("basic_json", limit=20)
        ids = [m["id"] for m in result["matches"]]
        self.assertIn("file:single_include/nlohmann/json", ids)
        self.assertIn("file:include/nlohmann/json", ids)
        # Amalgamation is the import surface; it must outrank the
        # modular peer on a single-token navigation query.
        self.assertLess(
            ids.index("file:single_include/nlohmann/json"),
            ids.index("file:include/nlohmann/json"),
        )

    def test_amalgamation_marker_set_on_synthetic_node(self) -> None:
        # Smoke check: the fixture itself declares the marker so the
        # rank assertion above is testing the boost.
        g = self._make_graph_with_amalgamation_pair()
        node = g._data["nodes"]["file:single_include/nlohmann/json"]
        self.assertTrue(node["props"].get("amalgamation"))


class NonCppRankingUnaffectedTest(unittest.TestCase):
    """Guard rail: non-cpp nodes (no marker) rank exactly as before."""

    def test_service_nodes_rank_by_id_tiebreak(self) -> None:
        nodes = {
            "service:b": {
                "type": "service", "label": "Router",
                "props": {"authority": "canonical", "confidence": "definite"},
            },
            "service:a": {
                "type": "service", "label": "Router",
                "props": {"authority": "canonical", "confidence": "definite"},
            },
        }
        ranked = rank_query_matches(
            list(nodes.items()), [["router"]],
            BM25Corpus.from_nodes(nodes),
            structural_scores={k: 0.0 for k in nodes},
        )
        # Lexicographic id tiebreak still wins.
        self.assertEqual(ranked[0][0], "service:a")
        self.assertEqual(ranked[1][0], "service:b")


class ExtractEndToEndStampsAmalgamationTest(unittest.TestCase):
    """``tree_sitter.extract`` stamps the marker for single_include paths."""

    def test_extract_stamps_amalgamation_for_single_include(self) -> None:
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "single_include" / "nlohmann").mkdir(parents=True)
            (root / "single_include" / "nlohmann" / "json.hpp").write_text(
                "/* synthetic amalgamation */\n"
                "namespace nlohmann { class basic_json {}; }\n",
            )
            with mock.patch.object(
                tree_sitter, "TREE_SITTER_AVAILABLE", True,
            ), mock.patch.object(
                tree_sitter, "_parse_file_symbols",
                return_value={
                    "exports": ["basic_json"],
                    "classes": ["basic_json"],
                    "imports": [],
                },
            ):
                result = tree_sitter.extract(
                    root=root,
                    source={"glob": "**/*.hpp", "language": "cpp"},
                    context={},
                )
        file_nodes = [n for n in result.nodes.values() if n["type"] == "file"]
        self.assertTrue(file_nodes, "expected at least one file node")
        self.assertTrue(
            file_nodes[0]["props"].get("amalgamation"),
            "single_include path must receive the amalgamation marker",
        )

    def test_extract_does_not_stamp_amalgamation_for_modular_path(self) -> None:
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "include" / "nlohmann").mkdir(parents=True)
            (root / "include" / "nlohmann" / "json.hpp").write_text(
                "namespace nlohmann { class basic_json {}; }\n",
            )
            with mock.patch.object(
                tree_sitter, "TREE_SITTER_AVAILABLE", True,
            ), mock.patch.object(
                tree_sitter, "_parse_file_symbols",
                return_value={
                    "exports": ["basic_json"],
                    "classes": ["basic_json"],
                    "imports": [],
                },
            ):
                result = tree_sitter.extract(
                    root=root,
                    source={"glob": "**/*.hpp", "language": "cpp"},
                    context={},
                )
        file_nodes = [n for n in result.nodes.values() if n["type"] == "file"]
        self.assertTrue(file_nodes, "expected at least one file node")
        self.assertFalse(
            file_nodes[0]["props"].get("amalgamation", False),
            "modular path must NOT receive the amalgamation marker",
        )


if __name__ == "__main__":
    unittest.main()
