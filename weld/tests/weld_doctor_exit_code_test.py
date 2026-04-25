"""Exit-code contract tests for ``wd doctor``.

``wd doctor`` must return:

- ``0`` when the setup is healthy (all checks OK) or when only warnings
  are reported (warnings are visible but not fatal). A directory with no
  Weld project initialized yet is a warning-only first-run state.
- ``1`` when any check returns ``fail`` -- for example a missing
  ``.weld/discover.yaml`` in a declared Weld project, a corrupt
  ``.weld/graph.json``, or an unresolved strategy reference.

The exit codes must also be documented in ``wd doctor --help`` so that
users and CI scripts can rely on the contract.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


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


def _setup_healthy(root: Path, *, mcp: bool = True) -> None:
    (root / ".weld").mkdir(exist_ok=True)
    (root / ".weld" / "discover.yaml").write_text(_minimal_discover_yaml(2))
    nodes = {
        f"n{i}": {"id": f"n{i}", "type": "file", "label": f"n{i}", "props": {}}
        for i in range(3)
    }
    (root / ".weld" / "graph.json").write_text(
        _minimal_graph(nodes=nodes, meta={"schema_version": 4, "git_sha": "abc"})
    )
    if mcp:
        (root / ".mcp.json").write_text('{"servers": {}}')


class DoctorExitZeroOnHealthy(unittest.TestCase):
    """Healthy setup -> exit code 0."""

    def test_healthy_project_exits_zero(self):
        from weld.doctor import main as doctor_main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_healthy(root)
            output = io.StringIO()
            with patch("weld.doctor.is_git_repo", return_value=True), \
                 patch("weld.doctor.get_git_sha", return_value="abc"), \
                 patch("weld.doctor.commits_behind", return_value=0), \
                 patch("sys.stdout", output):
                code = doctor_main(["--root", str(root)])
            self.assertEqual(code, 0)


class DoctorExitZeroOnWarningsOnly(unittest.TestCase):
    """Warnings are visible but not fatal -> exit code 0."""

    def test_stale_graph_and_missing_mcp_still_exit_zero(self):
        from weld.doctor import main as doctor_main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # No .mcp.json -> warn.  Stale git_sha -> warn.
            _setup_healthy(root, mcp=False)
            output = io.StringIO()
            with patch("weld.doctor.is_git_repo", return_value=True), \
                 patch("weld.doctor.get_git_sha", return_value="different-sha"), \
                 patch("weld.doctor.commits_behind", return_value=5), \
                 patch("sys.stdout", output):
                code = doctor_main(["--root", str(root)])
            self.assertEqual(code, 0)

    def test_missing_optional_dep_stays_warn_not_fail(self):
        """Missing optional Python deps report as warn and keep exit 0.

        Optional deps (mcp SDK, anthropic, openai, ...) are non-fatal by
        definition: weld still runs without them. Regression guard for
        the exit-code contract.
        """
        from weld.doctor import main as doctor_main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_healthy(root)
            output = io.StringIO()
            with patch(
                "weld._doctor_optional._module_available", return_value=False
            ), \
                 patch("weld.doctor.is_git_repo", return_value=True), \
                 patch("weld.doctor.get_git_sha", return_value="abc"), \
                 patch("weld.doctor.commits_behind", return_value=0), \
                 patch("sys.stdout", output):
                code = doctor_main(["--root", str(root)])
            self.assertEqual(code, 0)


class DoctorExitOneOnErrors(unittest.TestCase):
    """Any ``fail`` check -> exit code 1."""

    def test_missing_discover_yaml_in_declared_project(self):
        """`.weld/` exists but discover.yaml is absent -> exit 1."""
        from weld.doctor import main as doctor_main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".weld").mkdir()
            (root / ".weld" / "graph.json").write_text(_minimal_graph())
            output = io.StringIO()
            with patch("sys.stdout", output):
                code = doctor_main(["--root", str(root)])
            self.assertEqual(code, 1)

    def test_corrupt_graph_json(self):
        """Invalid JSON in graph.json -> exit 1."""
        from weld.doctor import main as doctor_main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".weld").mkdir()
            (root / ".weld" / "discover.yaml").write_text(_minimal_discover_yaml(1))
            (root / ".weld" / "graph.json").write_text("{not valid json")
            output = io.StringIO()
            with patch("sys.stdout", output):
                code = doctor_main(["--root", str(root)])
            self.assertEqual(code, 1)

    def test_missing_required_strategy_plugin(self):
        """Strategy referenced in discover.yaml but plugin absent -> exit 1.

        Represents the "missing required dep that breaks functionality"
        case: discovery cannot run for sources using an unresolved
        strategy, so doctor flags it as an error.
        """
        from weld.doctor import main as doctor_main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".weld").mkdir()
            (root / ".weld" / "discover.yaml").write_text(
                _minimal_discover_yaml(
                    2,
                    strategies=["python_module", "nonexistent_strategy_xyz"],
                )
            )
            (root / ".weld" / "graph.json").write_text(_minimal_graph())
            output = io.StringIO()
            with patch("sys.stdout", output):
                code = doctor_main(["--root", str(root)])
            self.assertEqual(code, 1)

    def test_missing_weld_dir_entirely(self):
        """No .weld/ directory at all -> exit 0 with first-run guidance."""
        from weld.doctor import main as doctor_main
        with tempfile.TemporaryDirectory() as td:
            output = io.StringIO()
            with patch("sys.stdout", output):
                code = doctor_main(["--root", td])
            self.assertEqual(code, 0)
            self.assertIn("No Weld project found", output.getvalue())


class DoctorHelpDocumentsExitCodes(unittest.TestCase):
    """`wd doctor --help` must describe the exit-code contract."""

    def _capture_help(self) -> str:
        from weld.doctor import main as doctor_main
        output = io.StringIO()
        with patch("sys.stdout", output):
            try:
                doctor_main(["--help"])
            except SystemExit:
                pass
        return output.getvalue()

    def test_help_mentions_exit_codes_section(self):
        text = self._capture_help()
        self.assertIn("Exit code", text)

    def test_help_documents_zero_and_one(self):
        text = self._capture_help()
        self.assertIn("0", text)
        self.assertIn("1", text)
        lowered = text.lower()
        self.assertTrue(
            "healthy" in lowered or "ok" in lowered,
            "help should describe the healthy/OK exit-0 case",
        )
        self.assertTrue(
            "error" in lowered or "invalid" in lowered,
            "help should describe the error/invalid exit-1 case",
        )


if __name__ == "__main__":
    unittest.main()
