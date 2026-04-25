"""Unit tests for the trust-posture engine (ADR 0025).

Covers:
- Healthy workspace -> risk='low', no high signals.
- Risky workspace (project-local strategy + external_json) -> risk='high'.
- Network enrichment env hint -> risk at least 'medium'.
- JSON shape pinning: required keys and allowed level vocabulary.
- Recommendations include the --safe guidance when high signals fire.
- MCP importability is reported as ok when ``weld.mcp_server`` imports.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from weld._security_posture import assess, to_json


def _write_discover_yaml(weld_dir: Path, *, with_external_json: bool = False) -> None:
    weld_dir.mkdir(parents=True, exist_ok=True)
    lines = ["sources:"]
    lines.append('  - glob: "src/**/*.py"')
    lines.append("    type: file")
    lines.append("    strategy: python_module")
    if with_external_json:
        lines.append('  - glob: "ext/**/*.json"')
        lines.append("    type: file")
        lines.append("    strategy: external_json")
        lines.append('    command: "/usr/bin/echo hi"')
    (weld_dir / "discover.yaml").write_text("\n".join(lines) + "\n")


def _write_graph(weld_dir: Path) -> None:
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "graph.json").write_text(
        '{"meta":{"schema_version":4},"nodes":{},"edges":[]}'
    )


class HealthyWorkspaceTest(unittest.TestCase):
    """A workspace with no project-local code execution surface is low risk."""

    def test_healthy_workspace_is_low_risk(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            weld_dir = root / ".weld"
            _write_discover_yaml(weld_dir, with_external_json=False)
            _write_graph(weld_dir)

            with patch.dict(os.environ, {}, clear=False):
                # Make sure no enrichment env hint leaks in from the harness.
                os.environ.pop("WELD_ENRICH_PROVIDER", None)
                report = assess(root)

            self.assertEqual(report.risk, "low")
            self.assertFalse(
                any(s.level == "high" for s in report.signals),
                msg=f"unexpected high signals in healthy workspace: {report.signals}",
            )


class RiskyWorkspaceTest(unittest.TestCase):
    """Project-local strategies and external_json adapters are high risk."""

    def test_project_local_strategy_marks_workspace_high_risk(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            weld_dir = root / ".weld"
            _write_discover_yaml(weld_dir, with_external_json=False)
            _write_graph(weld_dir)
            (weld_dir / "strategies").mkdir()
            (weld_dir / "strategies" / "custom.py").write_text("# evil\n")

            report = assess(root)

            self.assertEqual(report.risk, "high")
            ids = {s.id for s in report.signals}
            self.assertIn("project_local_strategies", ids)

    def test_external_json_marks_workspace_high_risk(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            weld_dir = root / ".weld"
            _write_discover_yaml(weld_dir, with_external_json=True)
            _write_graph(weld_dir)

            report = assess(root)

            self.assertEqual(report.risk, "high")
            ids = {s.id for s in report.signals}
            self.assertIn("external_json_adapters", ids)

    def test_high_risk_recommendations_mention_safe_mode(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            weld_dir = root / ".weld"
            _write_discover_yaml(weld_dir, with_external_json=True)
            _write_graph(weld_dir)

            report = assess(root)

            joined = " | ".join(report.recommendations)
            self.assertIn("--safe", joined)


class NetworkEnvTest(unittest.TestCase):
    """A configured WELD_ENRICH_PROVIDER pushes risk to at least medium."""

    def test_network_env_is_at_least_medium(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            weld_dir = root / ".weld"
            _write_discover_yaml(weld_dir)
            _write_graph(weld_dir)

            with patch.dict(os.environ, {"WELD_ENRICH_PROVIDER": "anthropic"}):
                report = assess(root)

            self.assertIn(report.risk, {"medium", "high"})
            ids = {s.id for s in report.signals}
            self.assertIn("enrichment_provider_env", ids)


class MCPImportabilityTest(unittest.TestCase):
    """The engine reports ok when weld.mcp_server imports without raising."""

    def test_mcp_importability_ok(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            weld_dir = root / ".weld"
            _write_discover_yaml(weld_dir)
            _write_graph(weld_dir)

            report = assess(root)

            mcp_signals = [s for s in report.signals if s.id == "mcp_importable"]
            self.assertTrue(mcp_signals, "expected an mcp_importable signal")
            self.assertEqual(mcp_signals[0].level, "ok")


class JsonShapeTest(unittest.TestCase):
    """Pin the JSON contract called out in ADR 0025."""

    _ALLOWED_LEVELS = {"ok", "warn", "high"}

    def test_json_shape_has_top_level_keys(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            weld_dir = root / ".weld"
            _write_discover_yaml(weld_dir)
            _write_graph(weld_dir)

            payload = to_json(assess(root))

            self.assertIn("risk", payload)
            self.assertIn("signals", payload)
            self.assertIn("recommendations", payload)
            self.assertIn(payload["risk"], {"low", "medium", "high"})
            self.assertIsInstance(payload["signals"], list)
            self.assertIsInstance(payload["recommendations"], list)

    def test_json_signal_records_have_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            weld_dir = root / ".weld"
            _write_discover_yaml(weld_dir, with_external_json=True)
            _write_graph(weld_dir)
            (weld_dir / "strategies").mkdir()
            (weld_dir / "strategies" / "custom.py").write_text("# x\n")

            payload = to_json(assess(root))

            for record in payload["signals"]:
                self.assertIn("id", record)
                self.assertIn("level", record)
                self.assertIn("section", record)
                self.assertIn("message", record)
                self.assertIn(record["level"], self._ALLOWED_LEVELS)


class NoWeldDirTest(unittest.TestCase):
    """Bare directories have no code-execution risk (no project-local
    strategies, no external_json), but graph-backed MCP tools cannot read
    anything yet -- the engine surfaces that as ``medium``.
    """

    def test_no_weld_dir_has_no_high_signals(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("WELD_ENRICH_PROVIDER", None)
                report = assess(root)
            self.assertNotEqual(report.risk, "high")
            self.assertFalse(any(s.level == "high" for s in report.signals))


if __name__ == "__main__":
    unittest.main()
