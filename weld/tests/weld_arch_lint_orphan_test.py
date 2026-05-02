"""Tests for the orphan-detection default suppression and CLI integration.

Covers ``weld.arch_lint_orphan`` (suppression of doc/config/test node
types), the ``--include-noisy`` flag, the new signal-first text
formatter, and the suppressed-only exit-code behaviour.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.contract import SCHEMA_VERSION  # noqa: E402


def _write_graph(root: Path, nodes: dict, edges: list) -> None:
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    (root / ".weld" / "graph.json").write_text(
        json.dumps(
            {
                "meta": {
                    "version": SCHEMA_VERSION,
                    "git_sha": "deadbeef",
                    "updated_at": "2026-04-15T00:00:00+00:00",
                },
                "nodes": nodes,
                "edges": edges,
            }
        ),
        encoding="utf-8",
    )


def _mixed_orphan_graph() -> tuple[dict, list]:
    """Graph with one symbol orphan + three suppressible orphans."""
    nodes = {
        "symbol:py:pkg.dead": {
            "type": "symbol", "label": "pkg.dead",
            "props": {"file": "pkg/dead.py", "qualname": "dead"},
        },
        "doc:docs/orphan-doc": {
            "type": "doc", "label": "Orphan Doc",
            "props": {"file": "docs/orphan-doc.md"},
        },
        "config:CFG_yaml": {
            "type": "config", "label": "CFG.yaml",
            "props": {"file": "CFG.yaml"},
        },
        "file:pkg.dead_test": {
            "type": "file", "label": "pkg/dead_test.py",
            "props": {"file": "pkg/dead_test.py"},
        },
    }
    return nodes, []


def _cycle_plus_orphan_graph() -> tuple[dict, list]:
    """Graph with one self-loop SCC + one symbol orphan."""
    nodes = {
        "symbol:py:pkg.cyc": {
            "type": "symbol", "label": "cyc",
            "props": {"file": "pkg/cyc.py"},
        },
        "symbol:py:pkg.dead": {
            "type": "symbol", "label": "dead",
            "props": {"file": "pkg/dead.py"},
        },
    }
    edges = [
        {"from": "symbol:py:pkg.cyc", "to": "symbol:py:pkg.cyc",
         "type": "calls", "props": {}},
    ]
    return nodes, edges


class OrphanDefaultSuppressionTest(unittest.TestCase):
    """Default orphan-detection suppresses doc/config/test node types."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def test_default_suppresses_doc_config_test_orphans(self) -> None:
        from weld.arch_lint import lint
        from weld.graph import Graph

        _write_graph(self.root, *_mixed_orphan_graph())
        g = Graph(self.root)
        g.load()
        result = lint(g, rule_ids=["orphan-detection"])

        ids = [v["node_id"] for v in result["violations"]]
        self.assertEqual(ids, ["symbol:py:pkg.dead"])
        self.assertEqual(result.get("suppressed_count"), 3)

    def test_include_noisy_restores_all_orphans(self) -> None:
        from weld.arch_lint import lint
        from weld.graph import Graph

        _write_graph(self.root, *_mixed_orphan_graph())
        g = Graph(self.root)
        g.load()
        result = lint(
            g, rule_ids=["orphan-detection"], include_noisy=True
        )
        self.assertEqual(result["violation_count"], 4)
        self.assertEqual(result.get("suppressed_count", 0), 0)

    def test_test_path_variants_all_suppressed(self) -> None:
        from weld.arch_lint import lint
        from weld.graph import Graph

        nodes = {
            "file:py_test": {
                "type": "file", "label": "py",
                "props": {"file": "pkg/foo_test.py"},
            },
            "file:go_test": {
                "type": "file", "label": "go",
                "props": {"file": "pkg/foo_test.go"},
            },
            "file:ts_test": {
                "type": "file", "label": "ts",
                "props": {"file": "src/foo.test.ts"},
            },
            "file:tests_dir": {
                "type": "file", "label": "td",
                "props": {"file": "pkg/tests/helper.py"},
            },
            "file:test_prefix": {
                "type": "file", "label": "tp",
                "props": {"file": "pkg/test_helper.py"},
            },
        }
        _write_graph(self.root, nodes, [])
        g = Graph(self.root)
        g.load()
        result = lint(g, rule_ids=["orphan-detection"])
        self.assertEqual(result["violation_count"], 0)
        self.assertEqual(result.get("suppressed_count"), 5)


class FormatOrderingTest(unittest.TestCase):
    """Text format prints summary first; signal rules before noisy rules."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def test_summary_line_present_at_top(self) -> None:
        from weld.arch_lint import format_text, lint
        from weld.graph import Graph

        _write_graph(self.root, *_cycle_plus_orphan_graph())
        g = Graph(self.root)
        g.load()
        text = format_text(lint(g))
        self.assertIn("violation", text.splitlines()[0].lower())

    def test_signal_rules_print_before_noisy(self) -> None:
        from weld.arch_lint import format_text, lint
        from weld.graph import Graph

        _write_graph(self.root, *_cycle_plus_orphan_graph())
        g = Graph(self.root)
        g.load()
        text = format_text(lint(g))
        cyc_pos = text.find("no-circular-deps")
        orphan_pos = text.find("orphan-detection")
        self.assertGreater(cyc_pos, -1)
        self.assertGreater(orphan_pos, -1)
        self.assertLess(cyc_pos, orphan_pos)


class ExitCodeWithSuppressedTest(unittest.TestCase):
    """Exit 0 when only suppressed orphans fire; non-zero otherwise."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def test_exit_zero_when_only_suppressed_orphans(self) -> None:
        from weld.arch_lint import main

        nodes = {
            "doc:docs/orphan-doc": {
                "type": "doc", "label": "Orphan Doc",
                "props": {"file": "docs/orphan-doc.md"},
            },
            "config:CFG_yaml": {
                "type": "config", "label": "CFG.yaml",
                "props": {"file": "CFG.yaml"},
            },
            "file:pkg.dead_test": {
                "type": "file", "label": "pkg/dead_test.py",
                "props": {"file": "pkg/dead_test.py"},
            },
        }
        _write_graph(self.root, nodes, [])
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["--root", str(self.root), "--json"])
        self.assertEqual(code, 0)

    def test_exit_nonzero_with_circular_dep_present(self) -> None:
        from weld.arch_lint import main

        _write_graph(self.root, *_cycle_plus_orphan_graph())
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["--root", str(self.root), "--json"])
        self.assertEqual(code, 1)

    def test_include_noisy_flag_restores_exit_one(self) -> None:
        from weld.arch_lint import main

        nodes = {
            "doc:docs/orphan-doc": {
                "type": "doc", "label": "Orphan Doc",
                "props": {"file": "docs/orphan-doc.md"},
            },
        }
        _write_graph(self.root, nodes, [])
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(
                ["--root", str(self.root), "--include-noisy", "--json"]
            )
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
