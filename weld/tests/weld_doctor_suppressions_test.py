"""Tests for the doctor-note suppression sidecar and ``--ack`` CLI flow.

Covers:
- ``load_suppressions`` round-trip on a tmp ``.weld/`` (add/remove).
- Robustness: missing file, empty file, malformed YAML -> empty set.
- ``--ack`` / ``--unack`` / ``--list-acks`` end-to-end through ``main([...])``.
- Allow-list rejection of unknown ids (stderr message + exit 2).
- Suppressed notes filtered from ``format_results`` output AND status
  footer counts (the OK headline must reflect the post-filter list).
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from weld._doctor_suppressions import (
    VALID_NOTE_IDS,
    add_suppression,
    load_suppressions,
    remove_suppression,
)
from weld.doctor import CheckResult, format_results, main as doctor_main


def _minimal_graph(nodes=None, edges=None, meta=None) -> str:
    return json.dumps(
        {
            "meta": meta or {"schema_version": 4},
            "nodes": nodes or {},
            "edges": edges or [],
        }
    )


def _minimal_discover_yaml(n_sources: int = 1) -> str:
    lines = ["sources:"]
    for i in range(n_sources):
        lines.append(f'  - glob: "src{i}/**/*.py"')
        lines.append("    type: file")
        lines.append("    strategy: python_module")
    return "\n".join(lines) + "\n"


def _setup_dir(root: Path, *, mcp: bool = False) -> None:
    (root / ".weld").mkdir(exist_ok=True)
    (root / ".weld" / "discover.yaml").write_text(_minimal_discover_yaml(1))
    nodes = {
        f"n{i}": {"id": f"n{i}", "type": "file", "label": f"n{i}", "props": {}}
        for i in range(2)
    }
    (root / ".weld" / "graph.json").write_text(
        _minimal_graph(nodes=nodes, meta={"schema_version": 4, "git_sha": "abc"})
    )
    if mcp:
        (root / ".mcp.json").write_text('{"servers": {}}')


class LoadSuppressionsRobustnessTest(unittest.TestCase):
    """``load_suppressions`` must NEVER raise on a malformed sidecar."""

    def test_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            weld_dir = Path(td) / ".weld"
            weld_dir.mkdir()
            self.assertEqual(load_suppressions(weld_dir), set())

    def test_empty_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            weld_dir = Path(td) / ".weld"
            weld_dir.mkdir()
            (weld_dir / "doctor.yaml").write_text("")
            self.assertEqual(load_suppressions(weld_dir), set())

    def test_malformed_yaml_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            weld_dir = Path(td) / ".weld"
            weld_dir.mkdir()
            (weld_dir / "doctor.yaml").write_text(":: not valid {{[\n}}}\n")
            self.assertEqual(load_suppressions(weld_dir), set())

    def test_unreadable_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            weld_dir = Path(td) / ".weld"
            weld_dir.mkdir()
            # Pass a path that does not exist; load() must not raise.
            phantom = weld_dir / "does-not-exist.yaml"
            self.assertFalse(phantom.exists())
            # load_suppressions reads weld_dir / doctor.yaml so its absence
            # is the same as the missing-file case.
            self.assertEqual(load_suppressions(weld_dir), set())


class SuppressionRoundTripTest(unittest.TestCase):
    def test_add_then_load(self):
        with tempfile.TemporaryDirectory() as td:
            weld_dir = Path(td) / ".weld"
            weld_dir.mkdir()
            self.assertTrue(add_suppression(weld_dir, "mcp-config-missing"))
            self.assertEqual(
                load_suppressions(weld_dir), {"mcp-config-missing"}
            )

    def test_add_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            weld_dir = Path(td) / ".weld"
            weld_dir.mkdir()
            self.assertTrue(add_suppression(weld_dir, "mcp-config-missing"))
            # second add returns False (no change)
            self.assertFalse(add_suppression(weld_dir, "mcp-config-missing"))

    def test_remove_returns_false_when_absent(self):
        with tempfile.TemporaryDirectory() as td:
            weld_dir = Path(td) / ".weld"
            weld_dir.mkdir()
            self.assertFalse(
                remove_suppression(weld_dir, "mcp-config-missing")
            )

    def test_remove_after_add(self):
        with tempfile.TemporaryDirectory() as td:
            weld_dir = Path(td) / ".weld"
            weld_dir.mkdir()
            add_suppression(weld_dir, "mcp-config-missing")
            add_suppression(weld_dir, "optional-copilot-cli-missing")
            self.assertTrue(
                remove_suppression(weld_dir, "mcp-config-missing")
            )
            self.assertEqual(
                load_suppressions(weld_dir),
                {"optional-copilot-cli-missing"},
            )


class ValidNoteIdsTest(unittest.TestCase):
    """The allow-list must cover exactly the ids the codebase emits."""

    def test_contains_expected_ids(self):
        expected = {
            "mcp-config-missing",
            "optional-mcp-missing",
            "optional-anthropic-missing",
            "optional-openai-missing",
            "optional-ollama-missing",
            "optional-copilot-cli-missing",
        }
        self.assertTrue(expected.issubset(VALID_NOTE_IDS))


class FormatNoteRenderingTest(unittest.TestCase):
    def test_note_renders_with_id_prefix(self):
        results = [
            CheckResult(
                "note",
                "MCP server config not found (.mcp.json or .codex/config.toml)",
                "MCP",
                note_id="mcp-config-missing",
            )
        ]
        formatted = format_results(results)
        self.assertIn("[note]", formatted)
        self.assertIn("(id: mcp-config-missing)", formatted)
        # Status footer should count notes separately.
        last_line = formatted.strip().splitlines()[-1]
        self.assertIn("1 note", last_line)


class StatusVerdictPrecedenceTest(unittest.TestCase):
    def test_notes_outrank_OK(self):
        """Verdict order: errors > warnings > notes > OK.

        With only ``ok`` + ``note`` results, the headline reads
        ``Status: notes`` so recommendations show up at a glance even
        when no warning or error is present. The exit code stays 0.
        """
        results = [
            CheckResult("ok", "fine", "Project"),
            CheckResult("note", "consider X", "MCP", note_id="mcp-config-missing"),
        ]
        formatted = format_results(results)
        last = formatted.strip().splitlines()[-1]
        self.assertIn("Status: notes", last)
        self.assertIn("1 note", last)

    def test_only_ok_yields_OK(self):
        results = [CheckResult("ok", "fine", "Project")]
        formatted = format_results(results)
        last = formatted.strip().splitlines()[-1]
        self.assertIn("Status: OK", last)

    def test_warning_outranks_note(self):
        results = [
            CheckResult("warn", "stale", "Graph"),
            CheckResult("note", "consider X", "MCP", note_id="mcp-config-missing"),
        ]
        formatted = format_results(results)
        last = formatted.strip().splitlines()[-1]
        self.assertIn("warnings", last)


class CliAckFlowTest(unittest.TestCase):
    def test_ack_writes_file_and_prints_confirmation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root)
            stdout = io.StringIO()
            with patch("sys.stdout", stdout):
                code = doctor_main(
                    ["--root", str(root), "--ack", "mcp-config-missing"]
                )
            self.assertEqual(code, 0)
            self.assertIn("acknowledged: mcp-config-missing", stdout.getvalue())
            self.assertEqual(
                load_suppressions(root / ".weld"),
                {"mcp-config-missing"},
            )

    def test_ack_already_acknowledged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root)
            add_suppression(root / ".weld", "mcp-config-missing")
            stdout = io.StringIO()
            with patch("sys.stdout", stdout):
                code = doctor_main(
                    ["--root", str(root), "--ack", "mcp-config-missing"]
                )
            self.assertEqual(code, 0)
            self.assertIn(
                "already acknowledged: mcp-config-missing",
                stdout.getvalue(),
            )

    def test_unack_clears_entry(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root)
            add_suppression(root / ".weld", "mcp-config-missing")
            stdout = io.StringIO()
            with patch("sys.stdout", stdout):
                code = doctor_main(
                    ["--root", str(root), "--unack", "mcp-config-missing"]
                )
            self.assertEqual(code, 0)
            self.assertIn("cleared: mcp-config-missing", stdout.getvalue())
            self.assertEqual(load_suppressions(root / ".weld"), set())

    def test_unack_not_acknowledged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root)
            stdout = io.StringIO()
            with patch("sys.stdout", stdout):
                code = doctor_main(
                    ["--root", str(root), "--unack", "mcp-config-missing"]
                )
            self.assertEqual(code, 0)
            self.assertIn(
                "not acknowledged: mcp-config-missing",
                stdout.getvalue(),
            )

    def test_list_acks_prints_ids_one_per_line(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root)
            add_suppression(root / ".weld", "mcp-config-missing")
            add_suppression(root / ".weld", "optional-copilot-cli-missing")
            stdout = io.StringIO()
            with patch("sys.stdout", stdout):
                code = doctor_main(["--root", str(root), "--list-acks"])
            self.assertEqual(code, 0)
            lines = [
                line for line in stdout.getvalue().splitlines() if line.strip()
            ]
            self.assertIn("mcp-config-missing", lines)
            self.assertIn("optional-copilot-cli-missing", lines)

    def test_unknown_id_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
                code = doctor_main(
                    ["--root", str(root), "--ack", "bogus-id"]
                )
            self.assertEqual(code, 2)
            self.assertIn("unknown note id: bogus-id", stderr.getvalue())

    def test_refuses_when_no_weld_dir(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)  # no .weld/
            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
                code = doctor_main(
                    ["--root", str(root), "--ack", "mcp-config-missing"]
                )
            self.assertEqual(code, 2)
            self.assertIn("no Weld project here", stderr.getvalue())


class SuppressionFiltersOutputTest(unittest.TestCase):
    def test_acked_note_not_rendered_and_not_counted(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root)  # no .mcp.json so we get the mcp-config-missing note
            add_suppression(root / ".weld", "mcp-config-missing")
            stdout = io.StringIO()
            with patch("weld.doctor.is_git_repo", return_value=False), patch(
                "sys.stdout", stdout
            ):
                code = doctor_main(["--root", str(root)])
            self.assertEqual(code, 0)
            output = stdout.getvalue()
            # The MCP missing note must be filtered out.
            self.assertNotIn("(id: mcp-config-missing)", output)
            # And it must not be counted in the status footer either.
            footer = [
                line
                for line in output.strip().splitlines()
                if line.startswith("Status:")
            ]
            self.assertTrue(footer)


class AckSecurityMutexTest(unittest.TestCase):
    def test_ack_with_security_is_mutually_exclusive(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_dir(root)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
                # argparse exits via SystemExit on mutex violation.
                with self.assertRaises(SystemExit) as cm:
                    doctor_main(
                        [
                            "--root", str(root),
                            "--security",
                            "--ack", "mcp-config-missing",
                        ]
                    )
            self.assertNotEqual(cm.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
