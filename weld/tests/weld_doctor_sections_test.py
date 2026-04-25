"""Tests for the new wd doctor section layout and PM-required fields.

Covers:
- enabled vs disabled strategies breakdown (discover.yaml ``enabled: false``).
- optional-deps summary (mcp SDK, anthropic, openai, ollama).
- PM section layout (Project / Config / Graph / Schema / Nodes / Edges /
  Strategies / Optional / MCP) and ``Status:`` footer.
- Security posture: no absolute root paths leaked.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from weld.doctor import doctor, format_results


def _minimal_graph(nodes=None, edges=None, meta=None):
    data = {
        "meta": meta or {"schema_version": 4},
        "nodes": nodes or {},
        "edges": edges or [],
    }
    return json.dumps(data)


def _minimal_discover_yaml(n_sources=2, strategies=None):
    strats = strategies or ["python_module"] * n_sources
    lines = ["sources:"]
    for i, strat in enumerate(strats):
        lines.append(f'  - glob: "src{i}/**/*.py"')
        lines.append("    type: file")
        lines.append(f"    strategy: {strat}")
    return "\n".join(lines) + "\n"


def _setup_dir(root, *, yaml_strategies=None, nodes=None, meta=None, mcp=False):
    (root / ".weld").mkdir(exist_ok=True)
    (root / ".weld" / "discover.yaml").write_text(
        _minimal_discover_yaml(strategies=yaml_strategies)
    )
    (root / ".weld" / "graph.json").write_text(
        _minimal_graph(nodes=nodes, meta=meta or {"schema_version": 4})
    )
    if mcp:
        (root / ".mcp.json").write_text('{"servers": {}}')


class DoctorEnabledDisabledStrategiesTest(unittest.TestCase):
    def test_reports_enabled_strategies(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(
                root,
                yaml_strategies=["python_module", "python_callgraph"],
            )
            results = doctor(root)
            enabled = [
                r for r in results
                if "enabled strategies" in r.message and r.level == "ok"
            ]
            self.assertTrue(enabled)
            self.assertIn("python_module", enabled[0].message)
            self.assertIn("python_callgraph", enabled[0].message)

    def test_reports_disabled_via_flag(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".weld").mkdir()
            (root / ".weld" / "discover.yaml").write_text(
                "sources:\n"
                '  - glob: "src/*.py"\n'
                "    type: file\n"
                "    strategy: python_module\n"
                '  - glob: "legacy/*.py"\n'
                "    type: file\n"
                "    strategy: manifest\n"
                "    enabled: false\n"
            )
            (root / ".weld" / "graph.json").write_text(_minimal_graph())
            results = doctor(root)
            disabled = [
                r for r in results
                if "disabled strategies" in r.message and r.level == "warn"
            ]
            self.assertTrue(disabled, "expected a disabled-strategies warn line")
            self.assertIn("manifest", disabled[0].message)
            enabled = [
                r for r in results
                if "enabled strategies" in r.message and r.level == "ok"
            ]
            self.assertTrue(enabled)
            self.assertIn("python_module", enabled[0].message)
            self.assertNotIn("manifest", enabled[0].message)

    def test_no_disabled_section_when_all_enabled(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root, yaml_strategies=["python_module"])
            results = doctor(root)
            disabled = [
                r for r in results if "disabled strategies" in r.message
            ]
            self.assertFalse(
                disabled,
                "no disabled line should appear when everything is enabled",
            )

    def test_strategy_enabled_in_one_source_wins_over_disabled(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".weld").mkdir()
            (root / ".weld" / "discover.yaml").write_text(
                "sources:\n"
                '  - glob: "a/*.py"\n'
                "    type: file\n"
                "    strategy: python_module\n"
                '  - glob: "b/*.py"\n'
                "    type: file\n"
                "    strategy: python_module\n"
                "    enabled: false\n"
            )
            (root / ".weld" / "graph.json").write_text(_minimal_graph())
            results = doctor(root)
            disabled = [
                r for r in results if "disabled strategies" in r.message
            ]
            self.assertFalse(disabled)


class DoctorOptionalDepsTest(unittest.TestCase):
    def test_reports_present_and_missing_split(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root)

            def fake_available(mod):
                return mod == "mcp"

            with patch(
                "weld._doctor_optional._module_available",
                side_effect=fake_available,
            ):
                results = doctor(root)

            optional = [r for r in results if r.section == "Optional"]
            present_lines = [
                r for r in optional
                if "optional deps present" in r.message and r.level == "ok"
            ]
            missing_lines = [
                r for r in optional
                if "optional deps missing" in r.message and r.level == "warn"
            ]
            self.assertTrue(present_lines)
            self.assertIn("mcp SDK", present_lines[0].message)
            self.assertTrue(missing_lines)
            self.assertIn("anthropic", missing_lines[0].message)
            self.assertIn("openai", missing_lines[0].message)
            self.assertIn("ollama", missing_lines[0].message)

    def test_missing_deps_hint_pip_install(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root)
            with patch(
                "weld._doctor_optional._module_available",
                return_value=False,
            ):
                results = doctor(root)
            hints = [
                r for r in results
                if r.section == "Optional"
                and "pip install" in r.message
                and r.level == "warn"
            ]
            self.assertGreaterEqual(len(hints), 4)
            self.assertTrue(any("weld[mcp]" in r.message for r in hints))
            self.assertTrue(any("weld[anthropic]" in r.message for r in hints))
            self.assertTrue(any("weld[openai]" in r.message for r in hints))
            self.assertTrue(any("weld[ollama]" in r.message for r in hints))

    def test_all_present_no_warn_spam(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root)
            with patch(
                "weld._doctor_optional._module_available",
                return_value=True,
            ):
                results = doctor(root)
            optional = [r for r in results if r.section == "Optional"]
            warns = [r for r in optional if r.level == "warn"]
            self.assertFalse(
                warns,
                f"unexpected optional warnings when all deps present: {warns}",
            )


class DoctorSectionLayoutTest(unittest.TestCase):
    def test_format_has_section_headers_in_order(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            nodes = {
                f"n{i}": {
                    "id": f"n{i}", "type": "file", "label": f"n{i}", "props": {}
                }
                for i in range(3)
            }
            _setup_dir(
                root,
                nodes=nodes,
                meta={"schema_version": 4, "git_sha": "abc"},
                mcp=True,
            )
            with patch("weld.doctor.is_git_repo", return_value=True), \
                 patch("weld.doctor.get_git_sha", return_value="abc"), \
                 patch("weld.doctor.commits_behind", return_value=0):
                results = doctor(root)
            formatted = format_results(results)
            expected_order = [
                "[Project]", "[Config]", "[Graph]", "[Schema]",
                "[Nodes]", "[Edges]", "[Strategies]", "[Optional]", "[MCP]",
            ]
            positions = []
            for header in expected_order:
                idx = formatted.find(header)
                self.assertGreaterEqual(
                    idx, 0, f"missing section header {header} in output"
                )
                positions.append(idx)
            self.assertEqual(
                positions, sorted(positions),
                f"section headers out of order: {formatted}",
            )

    def test_format_nests_results_under_sections(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root)
            results = doctor(root)
            formatted = format_results(results)
            self.assertIn("  [ok  ]", formatted)


class DoctorStatusLineTest(unittest.TestCase):
    def test_status_ok(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            nodes = {
                f"n{i}": {
                    "id": f"n{i}", "type": "file", "label": f"n{i}", "props": {}
                }
                for i in range(3)
            }
            _setup_dir(
                root,
                nodes=nodes,
                meta={"schema_version": 4, "git_sha": "abc"},
                mcp=True,
            )
            with patch("weld.doctor.is_git_repo", return_value=True), \
                 patch("weld.doctor.get_git_sha", return_value="abc"), \
                 patch("weld.doctor.commits_behind", return_value=0), \
                 patch(
                     "weld._doctor_optional._module_available",
                     return_value=True,
                 ):
                results = doctor(root)
            formatted = format_results(results)
            last_line = formatted.strip().splitlines()[-1]
            self.assertTrue(last_line.startswith("Status: "))
            self.assertIn("OK", last_line)
            self.assertIn("0 warnings", last_line)
            self.assertIn("0 errors", last_line)

    def test_status_errors_when_fail_present(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".weld").mkdir()
            results = doctor(root)
            formatted = format_results(results)
            last_line = formatted.strip().splitlines()[-1]
            self.assertTrue(last_line.startswith("Status: "))
            self.assertIn("errors", last_line)
            self.assertNotIn("0 errors", last_line)

    def test_status_warnings_when_no_fail(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root)
            results = doctor(root)
            self.assertTrue(any(r.level == "warn" for r in results))
            self.assertFalse(any(r.level == "fail" for r in results))
            formatted = format_results(results)
            last_line = formatted.strip().splitlines()[-1]
            self.assertTrue(last_line.startswith("Status: "))
            self.assertIn("warnings", last_line)
            self.assertIn("0 errors", last_line)


class DoctorSecurityPostureTest(unittest.TestCase):
    """Doctor output must not leak absolute paths or env info."""

    def test_no_absolute_root_path_in_output(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            _setup_dir(root)
            strategy_dir = root / ".weld" / "strategies"
            strategy_dir.mkdir()
            (strategy_dir / "custom.py").write_text("def extract(): pass\n")
            results = doctor(root)
            formatted = format_results(results)
            self.assertNotIn(str(root), formatted)
            for r in results:
                self.assertNotIn(str(root), r.message)


if __name__ == "__main__":
    unittest.main()
