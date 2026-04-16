"""Tests for ``weld.arch_lint`` -- architectural linting over the graph.

Covers the rule runner, the built-in ``orphan-detection`` rule, the
``strategy-coverage`` rule, the CLI wiring, and the exit-code contract
(0 pass, 1 violations).
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


def _connected_nodes() -> tuple[dict, list]:
    nodes = {
        "file:a.py": {"type": "file", "label": "a.py", "props": {"file": "a.py"}},
        "file:b.py": {"type": "file", "label": "b.py", "props": {"file": "b.py"}},
    }
    edges = [
        {"from": "file:a.py", "to": "file:b.py", "type": "imports", "props": {}},
    ]
    return nodes, edges


def _graph_with_orphan() -> tuple[dict, list]:
    nodes = {
        "file:a.py": {"type": "file", "label": "a.py", "props": {"file": "a.py"}},
        "file:b.py": {"type": "file", "label": "b.py", "props": {"file": "b.py"}},
        "file:dead.py": {
            "type": "file",
            "label": "dead.py",
            "props": {"file": "dead.py"},
        },
    }
    edges = [
        {"from": "file:a.py", "to": "file:b.py", "type": "imports", "props": {}},
    ]
    return nodes, edges


class RuleRunnerTest(unittest.TestCase):
    """Core ``lint()`` API: rule iteration, result envelope shape."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def test_returns_stable_envelope_on_clean_graph(self) -> None:
        from weld.arch_lint import lint
        from weld.graph import Graph

        nodes, edges = _connected_nodes()
        _write_graph(self.root, nodes, edges)
        graph = Graph(self.root)
        graph.load()

        result = lint(graph)

        self.assertEqual(result["arch_lint_version"], 1)
        self.assertIn("rules_run", result)
        self.assertIn("violations", result)
        self.assertEqual(result["violations"], [])
        self.assertEqual(result["violation_count"], 0)

    def test_returns_violations_when_orphans_present(self) -> None:
        from weld.arch_lint import lint
        from weld.graph import Graph

        nodes, edges = _graph_with_orphan()
        _write_graph(self.root, nodes, edges)
        graph = Graph(self.root)
        graph.load()

        result = lint(graph)

        self.assertEqual(result["violation_count"], 1)
        violation = result["violations"][0]
        self.assertEqual(violation["rule"], "orphan-detection")
        self.assertEqual(violation["node_id"], "file:dead.py")
        self.assertIn("message", violation)

    def test_rule_filter_limits_rules_run(self) -> None:
        from weld.arch_lint import lint
        from weld.graph import Graph

        nodes, edges = _graph_with_orphan()
        _write_graph(self.root, nodes, edges)
        graph = Graph(self.root)
        graph.load()

        # Unknown rule filter yields zero rules run and a warning.
        result = lint(graph, rule_ids=["no-such-rule"])

        self.assertEqual(result["rules_run"], [])
        self.assertEqual(result["violations"], [])
        self.assertTrue(
            any("no-such-rule" in w for w in result.get("warnings", [])),
            f"expected warning naming unknown rule, got {result.get('warnings')}",
        )

    def test_rule_filter_runs_only_selected_rule(self) -> None:
        from weld.arch_lint import lint
        from weld.graph import Graph

        nodes, edges = _graph_with_orphan()
        _write_graph(self.root, nodes, edges)
        graph = Graph(self.root)
        graph.load()

        result = lint(graph, rule_ids=["orphan-detection"])

        self.assertEqual(result["rules_run"], ["orphan-detection"])
        self.assertEqual(result["violation_count"], 1)


class OrphanDetectionRuleTest(unittest.TestCase):
    """The built-in ``orphan-detection`` rule."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def _lint_orphans(self, nodes: dict, edges: list) -> dict:
        from weld.arch_lint import lint
        from weld.graph import Graph
        _write_graph(self.root, nodes, edges)
        g = Graph(self.root)
        g.load()
        return lint(g, rule_ids=["orphan-detection"])

    def test_nodes_with_edges_not_orphan(self) -> None:
        r = self._lint_orphans(*_connected_nodes())
        self.assertEqual(r["violation_count"], 0)

    def test_multiple_orphans_each_reported(self) -> None:
        nodes = {
            "file:a.py": {"type": "file", "label": "a.py", "props": {"file": "a.py"}},
            "file:orphan1.py": {"type": "file", "label": "orphan1.py",
                                "props": {"file": "orphan1.py"}},
            "file:orphan2.py": {"type": "file", "label": "orphan2.py",
                                "props": {"file": "orphan2.py"}},
        }
        r = self._lint_orphans(nodes, [])
        self.assertEqual(r["violation_count"], 3)
        self.assertEqual(
            {v["node_id"] for v in r["violations"]},
            {"file:a.py", "file:orphan1.py", "file:orphan2.py"},
        )

    def test_violations_deterministic_order(self) -> None:
        nodes = {
            "file:z.py": {"type": "file", "label": "z.py", "props": {"file": "z.py"}},
            "file:a.py": {"type": "file", "label": "a.py", "props": {"file": "a.py"}},
            "file:m.py": {"type": "file", "label": "m.py", "props": {"file": "m.py"}},
        }
        r = self._lint_orphans(nodes, [])
        ids = [v["node_id"] for v in r["violations"]]
        self.assertEqual(ids, sorted(ids))


class CliExitCodeTest(unittest.TestCase):
    """CLI entry point exit code + output contract."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def test_clean_graph_exits_zero_json_output(self) -> None:
        from weld.arch_lint import main

        nodes, edges = _connected_nodes()
        _write_graph(self.root, nodes, edges)

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["--root", str(self.root), "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["violation_count"], 0)
        self.assertEqual(payload["arch_lint_version"], 1)

    def test_violations_exit_nonzero(self) -> None:
        from weld.arch_lint import main

        nodes, edges = _graph_with_orphan()
        _write_graph(self.root, nodes, edges)

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["--root", str(self.root), "--json"])
        self.assertEqual(code, 1)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["violation_count"], 1)

    def test_text_output_lists_violations(self) -> None:
        from weld.arch_lint import main

        nodes, edges = _graph_with_orphan()
        _write_graph(self.root, nodes, edges)

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["--root", str(self.root)])
        self.assertEqual(code, 1)
        text = buf.getvalue()
        # Human-readable output must name the offending node and rule id.
        self.assertIn("file:dead.py", text)
        self.assertIn("orphan-detection", text)

    def test_text_output_clean_graph(self) -> None:
        from weld.arch_lint import main

        nodes, edges = _connected_nodes()
        _write_graph(self.root, nodes, edges)

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["--root", str(self.root)])
        self.assertEqual(code, 0)
        # Clean-graph text output must not be empty and should read
        # as a positive confirmation.
        text = buf.getvalue().strip()
        self.assertTrue(text, "expected non-empty output on clean graph")

    def test_rule_flag_filters_rules(self) -> None:
        from weld.arch_lint import main

        nodes, edges = _graph_with_orphan()
        _write_graph(self.root, nodes, edges)

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(
                [
                    "--root",
                    str(self.root),
                    "--rule",
                    "orphan-detection",
                    "--json",
                ]
            )
        self.assertEqual(code, 1)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["rules_run"], ["orphan-detection"])


class CliDispatchTest(unittest.TestCase):
    """The top-level ``wd lint`` subcommand should dispatch to arch_lint."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def test_wd_lint_dispatches_to_arch_lint(self) -> None:
        from weld.cli import main as cli_main

        nodes, edges = _connected_nodes()
        _write_graph(self.root, nodes, edges)

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli_main(["lint", "--root", str(self.root), "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["arch_lint_version"], 1)


class StrategyCoverageRuleTest(unittest.TestCase):
    """The built-in ``strategy-coverage`` rule."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def _lint_coverage(self, yaml_text: str | None = None) -> dict:
        """Write graph + optional discover.yaml, run strategy-coverage."""
        from weld.arch_lint import lint
        from weld.graph import Graph

        _write_graph(self.root, *_connected_nodes())
        if yaml_text is not None:
            d = self.root / ".weld"
            d.mkdir(parents=True, exist_ok=True)
            (d / "discover.yaml").write_text(yaml_text, encoding="utf-8")
        g = Graph(self.root)
        g.load()
        return lint(g, rule_ids=["strategy-coverage"])

    def test_unmatched_glob_produces_violation(self) -> None:
        r = self._lint_coverage(
            'sources:\n  - glob: "nonexistent/*.py"\n'
            "    type: file\n    strategy: python_module\n"
        )
        self.assertEqual(r["violation_count"], 1)
        v = r["violations"][0]
        self.assertEqual(v["rule"], "strategy-coverage")
        self.assertEqual(v["node_id"], "nonexistent/*.py")
        self.assertIn("matched zero files", v["message"])
        self.assertEqual(v["severity"], "warning")

    def test_matched_glob_produces_no_violation(self) -> None:
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("# app", encoding="utf-8")
        r = self._lint_coverage(
            'sources:\n  - glob: "src/*.py"\n'
            "    type: file\n    strategy: python_module\n"
        )
        self.assertEqual(r["violation_count"], 0)

    def test_files_key_all_missing_produces_violation(self) -> None:
        r = self._lint_coverage(
            'sources:\n  - files: ["MISSING.md", "GONE.md"]\n'
            "    type: config\n    strategy: config_file\n"
        )
        self.assertEqual(r["violation_count"], 1)
        self.assertIn("files:", r["violations"][0]["node_id"])

    def test_files_key_partial_match_no_violation(self) -> None:
        (self.root / "README.md").write_text("# hi", encoding="utf-8")
        r = self._lint_coverage(
            'sources:\n  - files: ["README.md", "MISSING.md"]\n'
            "    type: config\n    strategy: config_file\n"
        )
        self.assertEqual(r["violation_count"], 0)

    def test_missing_discover_yaml_no_violation(self) -> None:
        self.assertEqual(self._lint_coverage()["violation_count"], 0)

    def test_multiple_unmatched_sorted_deterministically(self) -> None:
        r = self._lint_coverage(
            "sources:\n"
            '  - glob: "z_miss/*.py"\n    type: file\n    strategy: pm\n'
            '  - glob: "a_miss/*.py"\n    type: file\n    strategy: pm\n'
        )
        ids = [v["node_id"] for v in r["violations"]]
        self.assertEqual(ids, sorted(ids))

    def test_strategy_coverage_listed_in_available_rules(self) -> None:
        from weld.arch_lint import available_rule_ids
        self.assertIn("strategy-coverage", available_rule_ids())

    def test_recursive_glob_unmatched(self) -> None:
        r = self._lint_coverage(
            'sources:\n  - glob: "deep/**/*.rs"\n'
            "    type: file\n    strategy: rust_module\n"
        )
        self.assertEqual(r["violation_count"], 1)
        self.assertEqual(r["violations"][0]["node_id"], "deep/**/*.rs")


if __name__ == "__main__":
    unittest.main()
