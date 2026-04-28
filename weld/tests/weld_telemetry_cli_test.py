"""CLI integration tests for ``wd telemetry``.

Covers the seven subcommands defined by ADR 0035 § 4 ("``wd telemetry``
subcommand surface") plus the dispatch wiring in :mod:`weld.cli`. Each
case runs against a tempdir-backed telemetry file so on-disk effects
stay sandboxed and deterministic. Patterns mirror
``weld_brief_cli_test.py`` and ``weld_discover_output_test.py``: direct
``main()`` invocation with stdout/stderr redirection, no subprocess.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

# Make ``weld`` importable from a Bazel runfiles tree as well as from the
# repo root in local runs.
_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld import _telemetry as tel  # noqa: E402
from weld import _telemetry_allowlist as allowlist  # noqa: E402
from weld import telemetry_cli  # noqa: E402
from weld.cli import main as cli_main  # noqa: E402


# ---------------------------------------------------------------------------
# Test helpers.
# ---------------------------------------------------------------------------


_VALID_EVENT_TEMPLATE: dict = {
    "schema_version": 1,
    "ts": "2026-04-28T14:03:11Z",
    "weld_version": "0.10.5",
    "surface": "cli",
    "command": "discover",
    "outcome": "ok",
    "exit_code": 0,
    "duration_ms": 12,
    "error_kind": None,
    "python_version": "3.12.3",
    "platform": "linux",
    "flags": ["--json"],
}


def _make_repo(root: Path) -> Path:
    """Create a minimal single-repo project so resolve_path() finds it."""
    weld = root / ".weld"
    weld.mkdir(parents=True, exist_ok=True)
    (weld / "discover.yaml").write_text("# placeholder\n")
    return root


def _seed_events(path: Path, count: int) -> list[dict]:
    """Append ``count`` distinct, schema-valid events to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    events: list[dict] = []
    with path.open("a", encoding="utf-8") as f:
        for i in range(count):
            ev = dict(_VALID_EVENT_TEMPLATE)
            ev["duration_ms"] = i + 1
            f.write(json.dumps(ev, sort_keys=True) + "\n")
            events.append(ev)
    return events


@contextmanager
def _chdir(target: Path):
    prev = Path.cwd()
    os.chdir(target)
    try:
        yield
    finally:
        os.chdir(prev)


def _run(argv: list[str], *, cwd: Path | None = None,
         stdin: str | None = None) -> tuple[int, str, str]:
    """Run ``telemetry_cli.main(argv)`` capturing exit code, stdout, stderr.

    ``cwd`` controls the working directory used for path resolution. ``stdin``
    feeds the interactive ``clear`` prompt.
    """
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    in_buf = io.StringIO(stdin or "")
    ctx = _chdir(cwd) if cwd is not None else _noop_ctx()
    with ctx, mock.patch.object(sys, "stdout", out_buf), \
         mock.patch.object(sys, "stderr", err_buf), \
         mock.patch.object(sys, "stdin", in_buf):
        rc = telemetry_cli.main(argv)
    return rc, out_buf.getvalue(), err_buf.getvalue()


@contextmanager
def _noop_ctx():
    yield


# ---------------------------------------------------------------------------
# Allowlist & wiring.
# ---------------------------------------------------------------------------


class AllowlistExtensionTests(unittest.TestCase):
    """ADR 0035 § 5 — every subcommand records under a known command name."""

    def test_per_subcommand_command_names_are_allowlisted(self) -> None:
        for name in (
            "telemetry-status",
            "telemetry-show",
            "telemetry-path",
            "telemetry-export",
            "telemetry-clear",
            "telemetry-disable",
            "telemetry-enable",
        ):
            with self.subTest(name=name):
                self.assertIn(name, allowlist.CLI_COMMANDS)

    def test_top_level_telemetry_command_still_allowlisted(self) -> None:
        # Required by T3, but T2 must not regress the existing entry.
        self.assertIn("telemetry", allowlist.CLI_COMMANDS)


class CliDispatchTests(unittest.TestCase):
    """``wd telemetry status`` must dispatch through :mod:`weld.cli`."""

    def test_cli_dispatches_telemetry_subcommand(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            out_buf = io.StringIO()
            with _chdir(root), mock.patch.object(sys, "stdout", out_buf), \
                 mock.patch.object(sys, "stderr", io.StringIO()):
                rc = cli_main(["telemetry", "path"])
            self.assertEqual(rc, 0)
            # Path command emits exactly the resolved file path on stdout.
            printed = out_buf.getvalue().strip().splitlines()[0]
            self.assertTrue(printed.endswith("telemetry.jsonl"), printed)


# ---------------------------------------------------------------------------
# Subcommand-by-subcommand black-box.
# ---------------------------------------------------------------------------


class StatusTests(unittest.TestCase):
    def test_default_on_with_no_file_yet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            rc, out, _ = _run(["status"], cwd=root)
            self.assertEqual(rc, 0)
            self.assertIn("enabled: true", out.lower())
            self.assertIn(tel.OptOutSource.DEFAULT_ON.value, out)
            # File doesn't exist => 0 events / 0 bytes.
            self.assertIn("events: 0", out)
            self.assertIn("size_bytes: 0", out)

    def test_after_disable_reports_config_file_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            (root / ".weld" / "telemetry.disabled").touch()
            rc, out, _ = _run(["status"], cwd=root)
            self.assertEqual(rc, 0)
            self.assertIn("enabled: false", out.lower())
            self.assertIn(tel.OptOutSource.CONFIG_FILE.value, out)

    def test_status_reports_seeded_event_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            path = root / ".weld" / tel.TELEMETRY_FILENAME
            _seed_events(path, 3)
            rc, out, _ = _run(["status"], cwd=root)
            self.assertEqual(rc, 0)
            self.assertIn("events: 3", out)


class ShowTests(unittest.TestCase):
    def test_show_last_one_pretty_prints_latest_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            path = root / ".weld" / tel.TELEMETRY_FILENAME
            seeded = _seed_events(path, 2)
            rc, out, _ = _run(["show", "--last=1"], cwd=root)
            self.assertEqual(rc, 0)
            # Pretty (indent=2): contains newlines and a known field.
            self.assertIn("\n", out)
            self.assertIn('"command": "discover"', out)
            # Latest event has the latest duration_ms (we used i+1).
            self.assertIn(f'"duration_ms": {seeded[-1]["duration_ms"]}', out)

    def test_show_json_returns_raw_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            path = root / ".weld" / tel.TELEMETRY_FILENAME
            _seed_events(path, 2)
            rc, out, _ = _run(["show", "--last=1", "--json"], cwd=root)
            self.assertEqual(rc, 0)
            non_empty = [ln for ln in out.splitlines() if ln.strip()]
            self.assertEqual(len(non_empty), 1)
            parsed = json.loads(non_empty[0])
            self.assertEqual(parsed["surface"], "cli")

    def test_show_corrupt_line_yields_skeleton_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            path = root / ".weld" / tel.TELEMETRY_FILENAME
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as f:
                f.write("{not json\n")
                f.write(json.dumps(_VALID_EVENT_TEMPLATE) + "\n")
            rc, out, _ = _run(["show", "--last=2"], cwd=root)
            self.assertEqual(rc, 0)
            # Pretty-print contains the corrupt marker.
            self.assertIn('"_corrupt": true', out)

    def test_show_missing_file_returns_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            rc, out, _ = _run(["show"], cwd=root)
            self.assertEqual(rc, 0)
            # No events to show — empty primary output.
            self.assertNotIn('"command":', out)


class PathTests(unittest.TestCase):
    def test_path_prints_resolved_path_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            rc, out, _ = _run(["path"], cwd=root)
            self.assertEqual(rc, 0)
            lines = [ln for ln in out.splitlines() if ln.strip()]
            self.assertEqual(len(lines), 1)
            self.assertTrue(lines[0].endswith("telemetry.jsonl"))


class ExportTests(unittest.TestCase):
    def test_export_outside_weld_copies_bit_for_bit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_root, \
             tempfile.TemporaryDirectory() as tmp_dest:
            root = _make_repo(Path(tmp_root))
            path = root / ".weld" / tel.TELEMETRY_FILENAME
            _seed_events(path, 2)
            original = path.read_bytes()

            dest = Path(tmp_dest) / "share.jsonl"
            rc, _, _ = _run(["export", f"--output={dest}"], cwd=root)
            self.assertEqual(rc, 0)
            self.assertEqual(dest.read_bytes(), original)

    def test_export_into_weld_subdir_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            _seed_events(root / ".weld" / tel.TELEMETRY_FILENAME, 1)
            dest = root / ".weld" / "leak.jsonl"
            rc, _, err = _run(["export", f"--output={dest}"], cwd=root)
            self.assertNotEqual(rc, 0)
            self.assertIn(".weld", err)

    def test_export_into_nested_weld_refuses(self) -> None:
        # Any ``.weld`` ancestor must trigger refusal -- not just the project root.
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            _seed_events(root / ".weld" / tel.TELEMETRY_FILENAME, 1)
            nested = root / "subdir" / ".weld" / "deep" / "out.jsonl"
            rc, _, err = _run(["export", f"--output={nested}"], cwd=root)
            self.assertNotEqual(rc, 0)
            self.assertIn(".weld", err)

    def test_export_requires_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            rc, _, _ = _run(["export"], cwd=root)
            # argparse usage error -> exit 2.
            self.assertNotEqual(rc, 0)

    def test_export_when_source_missing_still_refuses_weld_destination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            dest = root / ".weld" / "x.jsonl"
            rc, _, err = _run(["export", f"--output={dest}"], cwd=root)
            self.assertNotEqual(rc, 0)
            self.assertIn(".weld", err)


class ClearTests(unittest.TestCase):
    def test_clear_yes_removes_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            path = root / ".weld" / tel.TELEMETRY_FILENAME
            _seed_events(path, 2)
            self.assertTrue(path.exists())
            rc, _, _ = _run(["clear", "--yes"], cwd=root)
            self.assertEqual(rc, 0)
            self.assertFalse(path.exists())
            # Status now reports 0 events (Recorder may have written one new
            # event for telemetry-clear, so be tolerant: 0 or 1, not 2).
            rc2, out, _ = _run(["status"], cwd=root)
            self.assertEqual(rc2, 0)
            # Event count must be strictly less than the seeded 2.
            for line in out.splitlines():
                if line.startswith("events:"):
                    count = int(line.split(":", 1)[1])
                    self.assertLess(count, 2)
                    break
            else:
                self.fail("status output missing events line")

    def test_clear_prompt_no_keeps_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            path = root / ".weld" / tel.TELEMETRY_FILENAME
            _seed_events(path, 1)
            rc, out, _ = _run(["clear"], cwd=root, stdin="n\n")
            self.assertEqual(rc, 0)
            self.assertTrue(path.exists())
            self.assertIn("Delete telemetry file?", out)

    def test_clear_prompt_yes_deletes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            path = root / ".weld" / tel.TELEMETRY_FILENAME
            _seed_events(path, 1)
            rc, _, _ = _run(["clear"], cwd=root, stdin="y\n")
            self.assertEqual(rc, 0)
            self.assertFalse(path.exists())

class DisableEnableTests(unittest.TestCase):
    def test_disable_creates_sentinel_then_status_reports_off(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            sentinel = root / ".weld" / "telemetry.disabled"
            self.assertFalse(sentinel.exists())
            rc, out, _ = _run(["disable"], cwd=root)
            self.assertEqual(rc, 0)
            self.assertTrue(sentinel.exists())
            self.assertIn("disabled", out.lower())
            # Status now shows source=config-file, enabled=False.
            rc2, status_out, _ = _run(["status"], cwd=root)
            self.assertEqual(rc2, 0)
            self.assertIn("enabled: false", status_out.lower())
            self.assertIn(tel.OptOutSource.CONFIG_FILE.value, status_out)

    def test_enable_removes_sentinel_then_status_reports_on(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            sentinel = root / ".weld" / "telemetry.disabled"
            sentinel.touch()
            rc, out, _ = _run(["enable"], cwd=root)
            self.assertEqual(rc, 0)
            self.assertFalse(sentinel.exists())
            self.assertIn("enabled", out.lower())
            rc2, status_out, _ = _run(["status"], cwd=root)
            self.assertEqual(rc2, 0)
            self.assertIn("enabled: true", status_out.lower())
            self.assertIn(tel.OptOutSource.DEFAULT_ON.value, status_out)

    def test_enable_when_no_sentinel_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            rc, _, _ = _run(["enable"], cwd=root)
            self.assertEqual(rc, 0)


# ---------------------------------------------------------------------------
# Help / unknown / argparse contracts.
# ---------------------------------------------------------------------------


class HelpAndUsageTests(unittest.TestCase):
    def test_no_subcommand_prints_help_and_exits_two(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            rc, out, err = _run([], cwd=root)
            # argparse: usage error -> exit 2; combined output mentions a
            # subcommand or "telemetry".
            self.assertEqual(rc, 2)
            combined = (out + err).lower()
            self.assertIn("telemetry", combined)

    def test_unknown_subcommand_exits_two(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            rc, _, _ = _run(["does-not-exist"], cwd=root)
            self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
