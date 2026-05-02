"""Tests for the wd doctor [Agent Graph] section.

Covers:
- Default ``wd doctor`` when ``.weld/agent-graph.json`` is present with
  zero diagnostics: ``[ok]`` line with the agent count.
- Default ``wd doctor`` when the agent-graph has broken-reference diagnostics:
  ``[warn]`` line with the diagnostic count and the suggested next command.
- Default ``wd doctor`` when ``.weld/agent-graph.json`` is missing: a
  ``[note]`` skip line that points at ``wd agents discover``.
- ``wd doctor --agent-graph`` flag prints only the [Agent Graph] section.
- Section ordering: ``[Agent Graph]`` appears in the formatted output.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from weld.doctor import doctor, format_results, main as doctor_main


def _minimal_graph_json(nodes=None, edges=None, meta=None):
    data = {
        "meta": meta or {"schema_version": 4},
        "nodes": nodes or {},
        "edges": edges or [],
    }
    return json.dumps(data)


def _minimal_discover_yaml():
    return (
        "sources:\n"
        '  - glob: "src/*.py"\n'
        "    type: file\n"
        "    strategy: python_module\n"
    )


def _setup_weld_dir(root):
    (root / ".weld").mkdir(exist_ok=True)
    (root / ".weld" / "discover.yaml").write_text(_minimal_discover_yaml())
    (root / ".weld" / "graph.json").write_text(_minimal_graph_json())


def _agent_node(name):
    return {
        "label": name,
        "type": "agent",
        "props": {"name": name, "platform": "claude"},
    }


def _write_agent_graph(root, *, agent_names, diagnostics=None):
    nodes = {f"agent:claude:{n}": _agent_node(n) for n in agent_names}
    payload = {
        "meta": {
            "version": 1,
            "updated_at": "2026-05-02T00:00:00+00:00",
            "discovered_from": [],
            "source_hashes": {},
            "diagnostics": diagnostics or [],
        },
        "nodes": nodes,
        "edges": [],
    }
    (root / ".weld" / "agent-graph.json").write_text(json.dumps(payload))


def _broken_ref_diag(path, line, target):
    return {
        "code": "agent_graph_broken_reference",
        "severity": "warning",
        "path": path,
        "line": line,
        "raw": target,
        "reference": target,
        "message": f"Referenced file does not exist: {target}",
        "source_node": "agent:claude:test",
    }


class DoctorAgentGraphPresentNoDiagnosticsTest(unittest.TestCase):
    def test_ok_line_shows_agent_count(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_weld_dir(root)
            _write_agent_graph(
                root,
                agent_names=["analyze", "tdd", "qa"],
                diagnostics=[],
            )
            results = doctor(root)
            ag = [r for r in results if r.section == "Agent Graph"]
            self.assertTrue(ag, "expected an Agent Graph section result")
            ok = [r for r in ag if r.level == "ok"]
            self.assertTrue(ok, "expected an [ok] line")
            self.assertIn("3", ok[0].message)
            self.assertIn("agent", ok[0].message.lower())
            warns = [r for r in ag if r.level == "warn"]
            self.assertFalse(warns, f"unexpected warnings: {warns}")


class DoctorAgentGraphWithDiagnosticsTest(unittest.TestCase):
    def test_warn_line_shows_diagnostic_count_and_command(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_weld_dir(root)
            diags = [
                _broken_ref_diag(
                    ".claude/agents/analyze.md",
                    81,
                    "weld/strategies/python_strategy.py",
                ),
                _broken_ref_diag(
                    ".claude/agents/architect.md",
                    37,
                    "docs/adrs/NNNN-short-title.md",
                ),
            ]
            _write_agent_graph(
                root,
                agent_names=["analyze", "architect"],
                diagnostics=diags,
            )
            results = doctor(root)
            ag = [r for r in results if r.section == "Agent Graph"]
            warns = [r for r in ag if r.level == "warn"]
            self.assertTrue(warns, "expected a warn line for diagnostics")
            joined = " ".join(r.message for r in warns)
            self.assertIn("2", joined)
            self.assertIn("broken-reference", joined.lower())
            # The hint should point users at the existing per-command surfacing.
            self.assertTrue(
                any("wd agents discover" in r.message for r in ag),
                f"expected pointer to wd agents discover in {ag}",
            )

    def test_warn_does_not_set_exit_code_via_fail_level(self):
        # The warn must remain warn (not fail); doctor exit code stays 0.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_weld_dir(root)
            _write_agent_graph(
                root,
                agent_names=["analyze"],
                diagnostics=[
                    _broken_ref_diag(
                        ".claude/agents/analyze.md",
                        10,
                        "missing.py",
                    ),
                ],
            )
            results = doctor(root)
            self.assertFalse(any(r.level == "fail" for r in results))


class DoctorAgentGraphMissingTest(unittest.TestCase):
    def test_skip_note_when_agent_graph_absent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_weld_dir(root)
            # Intentionally do NOT write .weld/agent-graph.json.
            results = doctor(root)
            ag = [r for r in results if r.section == "Agent Graph"]
            self.assertTrue(ag, "expected an Agent Graph section even when missing")
            self.assertTrue(
                any(r.level == "note" for r in ag),
                f"expected a note-level skip line in {ag}",
            )
            self.assertTrue(
                any("wd agents discover" in r.message for r in ag),
                f"expected pointer to wd agents discover in {ag}",
            )

    def test_unreadable_agent_graph_degrades_gracefully(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_weld_dir(root)
            (root / ".weld" / "agent-graph.json").write_text("{not json")
            results = doctor(root)
            ag = [r for r in results if r.section == "Agent Graph"]
            self.assertTrue(ag)
            # An unreadable file should produce a warn (not a hard fail), so
            # the doctor exit code stays 0 -- the file exists, it's just
            # malformed and the user should re-run discovery.
            self.assertTrue(any(r.level == "warn" for r in ag))
            self.assertFalse(any(r.level == "fail" for r in ag))


class DoctorAgentGraphSectionOrderTest(unittest.TestCase):
    def test_section_appears_in_formatted_output(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_weld_dir(root)
            _write_agent_graph(
                root,
                agent_names=["one", "two"],
                diagnostics=[],
            )
            results = doctor(root)
            formatted = format_results(results)
            self.assertIn("[Agent Graph]", formatted)


class DoctorAgentGraphFlagTest(unittest.TestCase):
    def test_flag_prints_only_agent_graph_section(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_weld_dir(root)
            _write_agent_graph(
                root,
                agent_names=["one", "two", "three"],
                diagnostics=[],
            )
            output = io.StringIO()
            with patch("sys.stdout", output):
                code = doctor_main(["--root", str(root), "--agent-graph"])
            self.assertEqual(code, 0)
            text = output.getvalue()
            self.assertIn("[Agent Graph]", text)
            # Other sections should NOT appear in --agent-graph mode.
            self.assertNotIn("[Project]", text)
            self.assertNotIn("[Config]", text)
            self.assertNotIn("[Optional]", text)

    def test_flag_when_missing_graph_emits_note(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_weld_dir(root)
            output = io.StringIO()
            with patch("sys.stdout", output):
                code = doctor_main(["--root", str(root), "--agent-graph"])
            # Note-level only -- exit code stays 0.
            self.assertEqual(code, 0)
            text = output.getvalue()
            self.assertIn("[Agent Graph]", text)
            self.assertIn("wd agents discover", text)


if __name__ == "__main__":
    unittest.main()
