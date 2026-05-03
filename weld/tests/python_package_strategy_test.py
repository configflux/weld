"""Regression: ``python_package`` strategy emits package -> file edges.

Closes the structural gap that ADR 0041 PR 3 papered over with a glob
allow-list (issue ``8ny3``). The ``python_module`` strategy emits
``file:`` anchors with outgoing ``contains`` edges (file -> exported
class / function) but no upstream strategy emits
``package:python:* -> contains -> file:*``. This test asserts the new
``python_package`` strategy fills that gap for both real packages
(directories with ``__init__.py``) and synthetic namespaces
(directories with no ``__init__.py`` such as ``tools/``).
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.strategies.python_package import extract  # noqa: E402


def _make_tree(td: Path) -> None:
    """Build a fixture tree with two real packages and one flat namespace.

    ``mypkg/__init__.py`` and ``mypkg/sub/__init__.py`` are real Python
    packages. ``scripts/`` has no ``__init__.py`` and represents the
    ``tools/`` synthetic namespace case.
    """
    (td / "mypkg").mkdir()
    (td / "mypkg" / "__init__.py").write_text("", encoding="utf-8")
    (td / "mypkg" / "alpha.py").write_text("X = 1\n", encoding="utf-8")
    (td / "mypkg" / "beta.py").write_text("Y = 2\n", encoding="utf-8")
    (td / "mypkg" / "sub").mkdir()
    (td / "mypkg" / "sub" / "__init__.py").write_text("", encoding="utf-8")
    (td / "mypkg" / "sub" / "gamma.py").write_text("Z = 3\n", encoding="utf-8")

    (td / "scripts").mkdir()
    (td / "scripts" / "tool_a.py").write_text("print('a')\n", encoding="utf-8")
    (td / "scripts" / "tool_b.py").write_text("print('b')\n", encoding="utf-8")


class PythonPackageStrategyTest(unittest.TestCase):
    """``python_package.extract`` must emit package nodes + contains edges."""

    def test_real_package_node_emitted(self) -> None:
        """A directory with ``__init__.py`` becomes a ``package:python:<name>``
        node with the dotted directory name."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tree(root)
            result = extract(root, {"glob": "mypkg/*.py"}, {})
        self.assertIn("package:python:mypkg", result.nodes)
        node = result.nodes["package:python:mypkg"]
        self.assertEqual(node["type"], "package")
        self.assertEqual(node["props"]["language"], "python")
        self.assertEqual(node["props"]["name"], "mypkg")
        self.assertFalse(node["props"]["synthetic"])

    def test_dotted_subpackage_name(self) -> None:
        """``mypkg/sub`` with ``__init__.py`` becomes
        ``package:python:mypkg.sub``."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tree(root)
            result = extract(root, {"glob": "mypkg/sub/*.py"}, {})
        self.assertIn("package:python:mypkg.sub", result.nodes)

    def test_contains_edges_emitted(self) -> None:
        """Every matched ``*.py`` file must receive a ``contains`` edge from
        its package."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tree(root)
            result = extract(root, {"glob": "mypkg/*.py"}, {})
        targets = {
            e["to"] for e in result.edges
            if e["from"] == "package:python:mypkg" and e["type"] == "contains"
        }
        # ``__init__.py`` and the two module files all get edges.
        self.assertIn("file:mypkg/alpha", targets)
        self.assertIn("file:mypkg/beta", targets)
        self.assertIn("file:mypkg/__init__", targets)

    def test_synthetic_package_for_flat_namespace(self) -> None:
        """A directory with NO ``__init__.py`` plus an explicit ``package``
        config field becomes a synthetic ``package:python:<name>`` node."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tree(root)
            result = extract(
                root,
                {"glob": "scripts/*.py", "package": "scripts"},
                {},
            )
        self.assertIn("package:python:scripts", result.nodes)
        node = result.nodes["package:python:scripts"]
        self.assertTrue(node["props"]["synthetic"])
        # Both scripts must be linked.
        targets = {
            e["to"] for e in result.edges
            if e["from"] == "package:python:scripts" and e["type"] == "contains"
        }
        self.assertEqual(targets, {"file:scripts/tool_a", "file:scripts/tool_b"})

    def test_edges_carry_strategy_metadata(self) -> None:
        """Every emitted edge must carry ``source_strategy=python_package``
        and ``confidence=definite`` so closure invariants and the
        agent-graph audit can attribute it correctly."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tree(root)
            result = extract(root, {"glob": "mypkg/*.py"}, {})
        for e in result.edges:
            self.assertEqual(e["props"]["source_strategy"], "python_package")
            self.assertEqual(e["props"]["confidence"], "definite")
            self.assertEqual(e["type"], "contains")

    def test_determinism_repeated_runs_identical(self) -> None:
        """Two extract() calls on the same tree must produce byte-identical
        node and edge lists -- ADR 0012 §3 graph determinism."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tree(root)
            r1 = extract(root, {"glob": "mypkg/*.py"}, {})
            r2 = extract(root, {"glob": "mypkg/*.py"}, {})
        self.assertEqual(r1.nodes, r2.nodes)
        self.assertEqual(r1.edges, r2.edges)
        self.assertEqual(r1.discovered_from, r2.discovered_from)

    def test_empty_match_returns_empty(self) -> None:
        """A glob that matches nothing must return empty results, not raise."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            result = extract(root, {"glob": "nonexistent/*.py"}, {})
        self.assertEqual(result.nodes, {})
        self.assertEqual(result.edges, [])

    def test_missing_glob_returns_empty(self) -> None:
        """A source with no ``glob`` is a no-op rather than a crash."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            result = extract(root, {}, {})
        self.assertEqual(result.nodes, {})
        self.assertEqual(result.edges, [])


if __name__ == "__main__":
    unittest.main()
