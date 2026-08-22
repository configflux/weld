"""Regression: ``go_package`` strategy emits package -> file edges.

Closes the Go half of the gap ``python_package``/``csharp_package``
already closed for Python and C# (ADR 0041 Layer 3
``file-anchor-symmetry``) and mints the producer-side node
``weld.cross_repo.package_import_resolver`` needs to resolve a genuine
Go cross-repo import (bd 1wcjp, following bd bt5m's diagnosis).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


from weld.contract import validate_node  # noqa: E402
from weld.strategies.go_package import extract  # noqa: E402


def _write_go_mod(root: Path, module_path: str) -> None:
    (root / "go.mod").write_text(f"module {module_path}\n\ngo 1.22\n", encoding="utf-8")


def _make_tree(td: Path) -> None:
    """Build a fixture tree with a root package and a nested one.

    ``mathutil/add.go`` is a real nested package. ``main.go`` sits at
    the module root. ``mathutil/empty.go`` has no declaration at all
    (only an unexported var), exercising the anchoring-member guard
    within a directory that otherwise DOES anchor.
    """
    _write_go_mod(td, "example.com/sample")
    (td / "main.go").write_text(
        "package main\n\nfunc main() {}\n", encoding="utf-8",
    )
    (td / "mathutil").mkdir()
    (td / "mathutil" / "add.go").write_text(
        "package mathutil\n\nfunc Add(a, b int) int {\n\treturn a + b\n}\n",
        encoding="utf-8",
    )
    (td / "mathutil" / "empty.go").write_text(
        "package mathutil\n\nvar unexported = 1\n", encoding="utf-8",
    )


class GoPackageStrategyTest(unittest.TestCase):
    """``go_package.extract`` must emit package nodes + contains edges."""

    def test_root_package_node_emitted(self) -> None:
        """A ``.go`` file at the module root becomes the bare module-path
        package (no directory suffix)."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tree(root)
            result = extract(root, {"glob": "main.go"}, {})
        self.assertIn("package:go:example.com-sample", result.nodes)
        node = result.nodes["package:go:example.com-sample"]
        self.assertEqual(node["type"], "package")
        self.assertEqual(node["props"]["language"], "go")
        self.assertEqual(node["props"]["name"], "example.com/sample")
        self.assertEqual(node["props"]["dir"], "")

    def test_nested_package_import_path(self) -> None:
        """``mathutil/`` becomes ``<module>/mathutil`` -- the directory
        path, never the ``package`` clause identifier."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tree(root)
            result = extract(root, {"glob": "mathutil/*.go"}, {})
        nid = "package:go:example.com-sample-mathutil"
        self.assertIn(nid, result.nodes)
        self.assertEqual(
            result.nodes[nid]["props"]["name"],
            "example.com/sample/mathutil",
        )

    def test_package_node_carries_explicit_origin(self) -> None:
        """ADR 0042: every emitted package node sets ``props.origin``
        directly -- the strategy only ever sees workspace-local globs, so
        origin is always ``project``."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tree(root)
            result = extract(root, {"glob": "mathutil/*.go"}, {})
        node = result.nodes["package:go:example.com-sample-mathutil"]
        self.assertEqual(node["props"]["origin"], "project")

    def test_contains_edges_emitted_for_all_members(self) -> None:
        """Every matched ``*.go`` file gets a ``contains`` edge from its
        package, including a member with no declaration of its own (the
        downstream de-dangle drops that specific edge if it never became
        a ``file:`` node; the group still anchors via its sibling)."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tree(root)
            result = extract(root, {"glob": "mathutil/*.go"}, {})
        targets = {
            e["to"] for e in result.edges
            if e["from"] == "package:go:example.com-sample-mathutil"
            and e["type"] == "contains"
        }
        self.assertEqual(
            targets, {"file:mathutil/add", "file:mathutil/empty"},
        )

    def test_edges_carry_strategy_metadata(self) -> None:
        """Every emitted edge must carry ``source_strategy=go_package``
        and ``confidence=definite``."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tree(root)
            result = extract(root, {"glob": "mathutil/*.go"}, {})
        for e in result.edges:
            self.assertEqual(e["props"]["source_strategy"], "go_package")
            self.assertEqual(e["props"]["confidence"], "definite")
            self.assertEqual(e["type"], "contains")

    def test_package_node_carries_membership_role(self) -> None:
        """``roles: ["package"]`` is the shape bd g7rs's purge and the
        ADR 0074 provenance-lint exemption both key off -- required for
        this strategy to compose with the existing generic mechanisms
        with no strategy-name-specific code."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tree(root)
            result = extract(root, {"glob": "mathutil/*.go"}, {})
        node = result.nodes["package:go:example.com-sample-mathutil"]
        self.assertEqual(node["props"]["roles"], ["package"])

    def test_determinism_repeated_runs_identical(self) -> None:
        """Two extract() calls on the same tree must produce byte-identical
        node and edge lists -- ADR 0012 §3 graph determinism."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tree(root)
            r1 = extract(root, {"glob": "**/*.go"}, {})
            r2 = extract(root, {"glob": "**/*.go"}, {})
        self.assertEqual(r1.nodes, r2.nodes)
        self.assertEqual(r1.edges, r2.edges)
        self.assertEqual(r1.discovered_from, r2.discovered_from)

    def test_empty_match_returns_empty(self) -> None:
        """A glob that matches nothing must return empty results, not raise."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_go_mod(root, "example.com/sample")
            result = extract(root, {"glob": "nonexistent/*.go"}, {})
        self.assertEqual(result.nodes, {})
        self.assertEqual(result.edges, [])

    def test_missing_glob_returns_empty(self) -> None:
        """A source with no ``glob`` is a no-op rather than a crash."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            result = extract(root, {}, {})
        self.assertEqual(result.nodes, {})
        self.assertEqual(result.edges, [])

    def test_missing_go_mod_returns_empty(self) -> None:
        """No ``go.mod`` means no import path can be derived -- skip
        entirely rather than guess, mirroring csharp_package's
        no-detectable-namespace skip."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "main.go").write_text(
                "package main\n\nfunc main() {}\n", encoding="utf-8",
            )
            result = extract(root, {"glob": "*.go"}, {})
        self.assertEqual(result.nodes, {})
        self.assertEqual(result.edges, [])
        self.assertEqual(result.discovered_from, [])

    def test_emitted_nodes_satisfy_the_contract(self) -> None:
        """Every node this strategy emits must pass ``validate_node``."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tree(root)
            result = extract(root, {"glob": "**/*.go"}, {})
        self.assertTrue(result.nodes, "fixture must emit at least one node")
        for node_id, node in result.nodes.items():
            self.assertEqual(
                validate_node(node_id, node), [],
                f"{node_id} violates the node contract",
            )


class GoPackageAnchorSymmetryTest(unittest.TestCase):
    """A package node must parent at least one file anchor (mirrors issue
    ``ddsy`` / bd g7rs for Go's own anchoring shape).

    Unlike ``python_package``'s single ``__init__.py`` special case, ANY
    Go file can be declaration-less (only unexported ``var``/``const``),
    so the guard is a per-file text scan rather than a filename check.
    """

    def test_declaration_less_dir_emits_nothing(self) -> None:
        """A directory whose only member has no func/type declaration at
        all gets no package node -- the shared tree-sitter pass would
        mint no ``file:`` node for it either."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_go_mod(root, "example.com/sample")
            (root / "consts").mkdir()
            (root / "consts" / "values.go").write_text(
                "package consts\n\nvar Unexported = 1\n", encoding="utf-8",
            )
            result = extract(root, {"glob": "consts/*.go"}, {})
        self.assertEqual(result.nodes, {})
        self.assertEqual(result.edges, [])
        self.assertEqual(result.discovered_from, [])

    def test_one_anchoring_member_is_enough(self) -> None:
        """A directory with one declaration-less file beside one real
        declaration still emits its package node with both files
        attached (the downstream de-dangle owns the individual dangling
        edge, if any)."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tree(root)
            result = extract(root, {"glob": "mathutil/*.go"}, {})
        self.assertIn("package:go:example.com-sample-mathutil", result.nodes)

    def test_suppressed_dir_does_not_shadow_sibling_packages(self) -> None:
        """A suppressed directory must not affect its siblings' emission
        under a recursive glob -- only its own node disappears."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tree(root)
            (root / "consts").mkdir()
            (root / "consts" / "values.go").write_text(
                "package consts\n\nvar Unexported = 1\n", encoding="utf-8",
            )
            result = extract(root, {"glob": "**/*.go"}, {})
        self.assertIn(
            "package:go:example.com-sample-mathutil", result.nodes,
        )
        self.assertNotIn(
            "package:go:example.com-sample-consts", result.nodes,
        )
        self.assertNotIn("consts/", result.discovered_from)

    def test_suppression_is_deterministic(self) -> None:
        """ADR 0012 §3: repeated runs over a tree containing a suppressed
        directory stay byte-identical."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tree(root)
            (root / "consts").mkdir()
            (root / "consts" / "values.go").write_text(
                "package consts\n\nvar Unexported = 1\n", encoding="utf-8",
            )
            r1 = extract(root, {"glob": "**/*.go"}, {})
            r2 = extract(root, {"glob": "**/*.go"}, {})
        self.assertEqual(r1.nodes, r2.nodes)
        self.assertEqual(r1.edges, r2.edges)
        self.assertEqual(r1.discovered_from, r2.discovered_from)
        self.assertNotIn(
            "package:go:example.com-sample-consts", r1.nodes,
        )


if __name__ == "__main__":
    unittest.main()
