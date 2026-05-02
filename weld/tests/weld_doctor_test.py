"""Tests for the wd doctor diagnostic command."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from weld.doctor import doctor


def _minimal_graph(nodes=None, edges=None, meta=None):
    data = {
        "meta": meta or {"schema_version": 4},
        "nodes": nodes or {},
        "edges": edges or [],
    }
    return json.dumps(data)


def _minimal_discover_yaml(n_sources=3, strategies=None):
    strats = strategies or ["python_module"] * n_sources
    lines = ["sources:"]
    for i, strat in enumerate(strats):
        lines.append(f'  - glob: "src{i}/**/*.py"')
        lines.append("    type: file")
        lines.append(f"    strategy: {strat}")
    return "\n".join(lines) + "\n"


def _setup_dir(
    root,
    n_sources=2,
    nodes=None,
    meta=None,
    mcp=False,
    codex_mcp=False,
    yaml_strategies=None,
):
    (root / ".weld").mkdir(exist_ok=True)
    (root / ".weld" / "discover.yaml").write_text(
        _minimal_discover_yaml(n_sources, strategies=yaml_strategies)
    )
    (root / ".weld" / "graph.json").write_text(
        _minimal_graph(nodes=nodes, meta=meta or {"schema_version": 4})
    )
    if mcp:
        (root / ".mcp.json").write_text('{"servers": {}}')
    if codex_mcp:
        codex_dir = root / ".codex"
        codex_dir.mkdir(exist_ok=True)
        (codex_dir / "config.toml").write_text("[mcp_servers.context7]\ncommand = \"npx\"\n")


class DoctorDiscoverYamlTest(unittest.TestCase):
    def test_ok_with_sources(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root, n_sources=5)
            results = doctor(root)
            yaml_r = [r for r in results if "discover.yaml" in r.message]
            self.assertTrue(yaml_r)
            self.assertEqual(yaml_r[0].level, "ok")
            self.assertIn("5 source", yaml_r[0].message)

    def test_fail_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".weld").mkdir()
            results = doctor(root)
            yaml_r = [r for r in results if "discover.yaml" in r.message]
            self.assertTrue(yaml_r)
            self.assertEqual(yaml_r[0].level, "fail")


class DoctorGraphTest(unittest.TestCase):
    def test_ok_with_counts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            nodes = {f"n{i}": {"id": f"n{i}", "type": "file", "label": f"n{i}", "props": {}} for i in range(5)}
            edges = [{"source": "n0", "target": "n1", "type": "imports"}, {"source": "n1", "target": "n2", "type": "imports"}]
            _setup_dir(root, nodes=nodes, meta={"schema_version": 4})
            (root / ".weld" / "graph.json").write_text(_minimal_graph(nodes=nodes, edges=edges, meta={"schema_version": 4}))
            results = doctor(root)
            ok_graph = [r for r in results if "graph.json" in r.message and r.level == "ok"]
            self.assertTrue(ok_graph)
            self.assertIn("5 node", ok_graph[0].message)
            self.assertIn("2 edge", ok_graph[0].message)
            self.assertIn("v4", ok_graph[0].message)

    def test_fail_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".weld").mkdir()
            (root / ".weld" / "discover.yaml").write_text(_minimal_discover_yaml(2))
            results = doctor(root)
            graph_r = [r for r in results if "graph.json" in r.message]
            self.assertTrue(graph_r)
            self.assertEqual(graph_r[0].level, "fail")


class DoctorStalenessTest(unittest.TestCase):
    def test_warn_stale(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root, meta={"schema_version": 4, "git_sha": "aaa"},
                       nodes={"n0": {"id": "n0", "type": "file", "label": "n0", "props": {}}})
            with patch("weld.doctor.is_git_repo", return_value=True), \
                 patch("weld.doctor.get_git_sha", return_value="bbb"), \
                 patch("weld.doctor.commits_behind", return_value=3):
                results = doctor(root)
            stale_r = [r for r in results if "behind" in r.message.lower() or "stale" in r.message.lower()]
            self.assertTrue(stale_r)
            self.assertEqual(stale_r[0].level, "warn")
            self.assertIn("3", stale_r[0].message)

    def test_ok_fresh(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root, meta={"schema_version": 4, "git_sha": "abc123"},
                       nodes={"n0": {"id": "n0", "type": "file", "label": "n0", "props": {}}})
            with patch("weld.doctor.is_git_repo", return_value=True), \
                 patch("weld.doctor.get_git_sha", return_value="abc123"), \
                 patch("weld.doctor.commits_behind", return_value=0):
                results = doctor(root)
            stale_r = [r for r in results if "behind" in r.message.lower()]
            self.assertFalse(stale_r)


class DoctorTreeSitterTest(unittest.TestCase):
    def test_ok_available(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root, n_sources=1, yaml_strategies=["tree_sitter"])
            with patch("weld.doctor._check_tree_sitter_language", return_value=True):
                results = doctor(root)
            ts_ok = [r for r in results if ("tree-sitter" in r.message.lower() or "tree_sitter" in r.message.lower()) and r.level == "ok"]
            self.assertTrue(ts_ok)

    def test_warn_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root, n_sources=1, yaml_strategies=["tree_sitter"])
            with patch("weld.doctor._check_tree_sitter_language", return_value=False):
                results = doctor(root)
            ts_warn = [r for r in results if ("tree-sitter" in r.message.lower() or "tree_sitter" in r.message.lower()) and r.level == "warn"]
            self.assertTrue(ts_warn)


class DoctorMcpConfigTest(unittest.TestCase):
    def test_ok_present(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root, mcp=True)
            results = doctor(root)
            ok_mcp = [r for r in results if "mcp" in r.message.lower() and r.level == "ok"]
            self.assertTrue(ok_mcp)

    def test_ok_with_codex_config(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root, codex_mcp=True)
            results = doctor(root)
            ok_mcp = [r for r in results if "mcp" in r.message.lower() and r.level == "ok"]
            self.assertTrue(ok_mcp)
            self.assertIn(".codex/config.toml", ok_mcp[0].message)

    def test_note_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root)
            results = doctor(root)
            # MCP-missing is now a recommendation-level note, not a warning.
            note_mcp = [
                r for r in results
                if "mcp" in r.message.lower()
                and r.level == "note"
                and getattr(r, "note_id", None) == "mcp-config-missing"
            ]
            self.assertTrue(note_mcp)


class DoctorStrategyTest(unittest.TestCase):
    def test_ok_all_found(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root, yaml_strategies=["python_module", "python_module"])
            results = doctor(root)
            ok_strat = [r for r in results if "strateg" in r.message.lower() and r.level == "ok"]
            self.assertTrue(ok_strat)

    def test_fail_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root, yaml_strategies=["python_module", "nonexistent_strategy_xyz"])
            results = doctor(root)
            fail_r = [r for r in results if r.level == "fail" and "strateg" in r.message.lower()]
            self.assertTrue(fail_r)
            self.assertIn("nonexistent_strategy_xyz", fail_r[0].message)


class DoctorTrustBoundaryTest(unittest.TestCase):
    def test_warns_for_project_local_strategies(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root)
            strategy_dir = root / ".weld" / "strategies"
            strategy_dir.mkdir()
            (strategy_dir / "custom.py").write_text("def extract(): pass\n")

            results = doctor(root)

            warnings = [
                r for r in results
                if r.level == "warn" and "trusted repos" in r.message
            ]
            self.assertTrue(warnings)
            self.assertIn("project-local strategies", warnings[0].message)

    def test_warns_for_external_json_sources(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root, yaml_strategies=["external_json"])

            results = doctor(root)

            warnings = [
                r for r in results
                if r.level == "warn" and "external_json" in r.message
            ]
            self.assertTrue(warnings)
            self.assertIn("execute configured commands", warnings[0].message)


class DoctorPythonVersionTest(unittest.TestCase):
    def test_ok_current(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root, n_sources=1)
            results = doctor(root)
            py_r = [r for r in results if "python" in r.message.lower()]
            self.assertTrue(py_r)
            self.assertEqual(py_r[0].level, "ok")

    def test_warn_old(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root, n_sources=1)
            with patch("weld.doctor.sys") as mock_sys:
                mock_sys.version_info = (3, 9, 1, "final", 0)
                mock_sys.version = "3.9.1 (default)"
                results = doctor(root)
            py_r = [r for r in results if "python" in r.message.lower()]
            self.assertTrue(py_r)
            self.assertEqual(py_r[0].level, "warn")


class DoctorExitCodeTest(unittest.TestCase):
    def test_exit_zero_without_weld_project(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            results = doctor(root)
            self.assertFalse(any(r.level == "fail" for r in results))
            self.assertTrue(
                any("No Weld project found" in r.message for r in results)
            )

    def test_exit_zero_no_fail(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            nodes = {f"n{i}": {"id": f"n{i}", "type": "file", "label": f"n{i}", "props": {}} for i in range(5)}
            _setup_dir(root, nodes=nodes, meta={"schema_version": 4, "git_sha": "abc"}, mcp=True)
            with patch("weld.doctor.is_git_repo", return_value=True), \
                 patch("weld.doctor.get_git_sha", return_value="abc"), \
                 patch("weld.doctor.commits_behind", return_value=0):
                results = doctor(root)
            self.assertFalse(any(r.level == "fail" for r in results))

    def test_exit_one_with_fail(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".weld").mkdir()
            results = doctor(root)
            self.assertTrue(any(r.level == "fail" for r in results))


class DoctorCliDispatchTest(unittest.TestCase):
    def test_cli_dispatches_doctor(self):
        from weld.cli import main as cli_main
        with tempfile.TemporaryDirectory() as td:
            output = io.StringIO()
            with patch("sys.stdout", output):
                cli_main(["doctor", "--root", td])
            self.assertGreater(len(output.getvalue()), 0)

    def test_help_mentions_doctor(self):
        from weld.cli import main as cli_main
        output = io.StringIO()
        with patch("sys.stdout", output):
            cli_main(["--help"])
        self.assertIn("doctor", output.getvalue())


class DoctorOutputFormatTest(unittest.TestCase):
    def test_format_tags(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root)
            results = doctor(root)
            for r in results:
                self.assertIn(r.level, ("ok", "note", "warn", "fail"))


class DoctorMainExitCodeTest(unittest.TestCase):
    def test_main_exit_fail(self):
        from weld.doctor import main as doctor_main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".weld").mkdir()
            output = io.StringIO()
            with patch("sys.stdout", output):
                code = doctor_main(["--root", td])
            self.assertEqual(code, 1)

    def test_main_exit_ok_without_weld_project(self):
        from weld.doctor import main as doctor_main
        with tempfile.TemporaryDirectory() as td:
            output = io.StringIO()
            with patch("sys.stdout", output):
                code = doctor_main(["--root", td])
            self.assertEqual(code, 0)
            self.assertIn("No Weld project found", output.getvalue())

    def test_main_exit_ok(self):
        from weld.doctor import main as doctor_main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            nodes = {f"n{i}": {"id": f"n{i}", "type": "file", "label": f"n{i}", "props": {}} for i in range(5)}
            _setup_dir(root, nodes=nodes, meta={"schema_version": 4, "git_sha": "abc"}, mcp=True)
            output = io.StringIO()
            with patch("weld.doctor.is_git_repo", return_value=True), \
                 patch("weld.doctor.get_git_sha", return_value="abc"), \
                 patch("weld.doctor.commits_behind", return_value=0), \
                 patch("sys.stdout", output):
                code = doctor_main(["--root", str(root)])
            self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
