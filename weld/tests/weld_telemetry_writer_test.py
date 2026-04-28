"""Unit tests for :mod:`weld._telemetry`.

Covers the Recorder context manager, opt-out resolution, polyrepo path
resolution, the failure-isolated writer, the 1 MiB rotation policy, and
the once-per-file first-run stderr notice (ADR 0035 sections 1, 3, 5, 6).
A ``_FakeClock`` mirrored from ``weld_watch_test.py`` makes
``duration_ms`` deterministic, and ``tempfile.TemporaryDirectory`` keeps
on-disk effects sandboxed.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from weld import _telemetry as tel


class _FakeClock:
    """Deterministic monotonic_ns substitute drive-able by tests."""

    def __init__(self, start_ns: int = 1_000_000_000) -> None:
        self.t_ns = start_ns

    def __call__(self) -> int:
        return self.t_ns

    def advance_ms(self, delta_ms: int) -> None:
        self.t_ns += delta_ms * 1_000_000


def _make_repo(root: Path) -> Path:
    """Create a minimal single-repo project so resolve_path() finds it."""
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    (root / ".weld" / "discover.yaml").write_text("# placeholder\n")
    return root


def _read_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _rec(root: Path, **overrides) -> tel.Recorder:
    """Build a Recorder with sensible test defaults."""
    kw: dict = {
        "surface": "cli",
        "command": "discover",
        "flags": [],
        "root": root,
        "clock": _FakeClock(),
        "stderr": io.StringIO(),
    }
    kw.update(overrides)
    return tel.Recorder(**kw)


class RecorderHappyPathTests(unittest.TestCase):
    def test_ok_outcome_event_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            clock = _FakeClock()
            with _rec(root, flags=["--json"], clock=clock) as rec:
                clock.advance_ms(250)
            self.assertEqual(rec.outcome, "ok")
            ev = _read_events(root / ".weld" / tel.TELEMETRY_FILENAME)[0]
            self.assertEqual(ev["surface"], "cli")
            self.assertEqual(ev["command"], "discover")
            self.assertEqual(ev["outcome"], "ok")
            self.assertEqual(ev["exit_code"], 0)
            self.assertEqual(ev["duration_ms"], 250)
            self.assertIsNone(ev["error_kind"])
            self.assertEqual(ev["schema_version"], tel.TELEMETRY_SCHEMA_VERSION)

    def test_command_coerced_to_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            with _rec(root, command="not-a-real-subcommand"):
                pass
            events = _read_events(root / ".weld" / tel.TELEMETRY_FILENAME)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["command"], "unknown")

    def test_flags_filtered_through_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            with _rec(root, flags=["--json", "/leaked/path", "--no-such-flag", "--json"]):
                pass
            events = _read_events(root / ".weld" / tel.TELEMETRY_FILENAME)
            self.assertEqual(events[0]["flags"], ["--json"])


class RecorderErrorPathTests(unittest.TestCase):
    def test_value_error_records_error_outcome_and_re_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            clock = _FakeClock()
            with self.assertRaises(ValueError):
                with _rec(root, clock=clock):
                    clock.advance_ms(10)
                    raise ValueError("opaque message must NOT appear")
            ev = _read_events(root / ".weld" / tel.TELEMETRY_FILENAME)[0]
            self.assertEqual(ev["outcome"], "error")
            self.assertEqual(ev["error_kind"], "ValueError")
            self.assertEqual(ev["exit_code"], 1)
            # Exception message must NEVER appear in the file.
            for value in ev.values():
                if isinstance(value, str):
                    self.assertNotIn("opaque message", value)

    def test_keyboard_interrupt_records_interrupted_with_exit_130(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            with self.assertRaises(KeyboardInterrupt):
                with _rec(root):
                    raise KeyboardInterrupt()
            ev = _read_events(root / ".weld" / tel.TELEMETRY_FILENAME)[0]
            self.assertEqual(ev["outcome"], "interrupted")
            self.assertEqual(ev["exit_code"], 130)
            self.assertEqual(ev["error_kind"], "KeyboardInterrupt")

    def test_broken_pipe_records_interrupted_with_exit_141(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            with self.assertRaises(BrokenPipeError):
                with _rec(root):
                    raise BrokenPipeError()
            ev = _read_events(root / ".weld" / tel.TELEMETRY_FILENAME)[0]
            self.assertEqual(ev["outcome"], "interrupted")
            self.assertEqual(ev["exit_code"], 141)


class RecorderFailureIsolationTests(unittest.TestCase):
    def test_writer_oserror_is_swallowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            with mock.patch.object(tel, "_write_locked", side_effect=OSError("full")):
                # No exception must escape the Recorder block.
                with _rec(root):
                    pass
            # File never created since the writer raised.
            self.assertFalse((root / ".weld" / tel.TELEMETRY_FILENAME).exists())

    def test_inner_return_value_preserved_when_writer_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            captured: list[int] = []
            with mock.patch.object(tel, "_write_locked", side_effect=RuntimeError("x")):
                with _rec(root):
                    captured.append(42)
            self.assertEqual(captured, [42])

    def test_recorder_set_exit_code_overrides_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            with _rec(root) as rec:
                rec.set_exit_code(2)
            events = _read_events(root / ".weld" / tel.TELEMETRY_FILENAME)
            self.assertEqual(events[0]["exit_code"], 2)


class RotationTests(unittest.TestCase):
    def test_file_rotated_to_keep_trailing_n_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            path = root / ".weld" / tel.TELEMETRY_FILENAME
            # Pre-populate with > 1 MiB of synthetic events. Each line is
            # roughly ~250 bytes -- aim for ~5000 to guarantee size > 1 MiB.
            path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps({
                "schema_version": 1,
                "ts": "2026-04-28T14:03:11Z",
                "weld_version": "0.10.5",
                "surface": "cli",
                "command": "discover",
                "outcome": "ok",
                "exit_code": 0,
                "duration_ms": 1,
                "error_kind": None,
                "python_version": "3.12.3",
                "platform": "linux",
                "flags": ["--json"],
                "_seq": 0,
            }) + "\n"
            target_bytes = tel.MAX_FILE_BYTES + 4096
            n_lines = (target_bytes // len(line)) + 1
            with path.open("w", encoding="utf-8") as f:
                for i in range(n_lines):
                    # Renumber _seq so we can verify which lines survived.
                    f.write(line.replace('"_seq": 0', f'"_seq": {i}'))
            self.assertGreater(path.stat().st_size, tel.MAX_FILE_BYTES)

            tel._rotate_if_needed(path)

            kept = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(kept), tel.MAX_EVENTS_KEPT_AFTER_ROTATE)
            # Trailing lines kept -- highest _seq is the very last one.
            last = json.loads(kept[-1])
            self.assertEqual(last["_seq"], n_lines - 1)
            first = json.loads(kept[0])
            self.assertEqual(
                first["_seq"], n_lines - tel.MAX_EVENTS_KEPT_AFTER_ROTATE
            )

    def test_rotation_skipped_when_under_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            path = root / ".weld" / tel.TELEMETRY_FILENAME
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("a\nb\nc\n")
            tel._rotate_if_needed(path)
            # Untouched.
            self.assertEqual(path.read_text(), "a\nb\nc\n")


class FirstRunNoticeTests(unittest.TestCase):
    def test_notice_printed_once_then_silent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            stderr1 = io.StringIO()
            with _rec(root, stderr=stderr1):
                pass
            self.assertIn("local telemetry on", stderr1.getvalue())
            self.assertIn("WELD_TELEMETRY", stderr1.getvalue())
            stderr2 = io.StringIO()
            with _rec(root, stderr=stderr2):
                pass
            self.assertEqual(stderr2.getvalue(), "")


class IsEnabledTests(unittest.TestCase):
    def test_cli_flag_takes_priority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            enabled, source = tel.is_enabled(cli_flag=False, root=root)
            self.assertFalse(enabled)
            self.assertEqual(source, tel.OptOutSource.CLI_FLAG)

    def test_env_var_off(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            with mock.patch.dict(os.environ, {"WELD_TELEMETRY": "off"}):
                enabled, source = tel.is_enabled(cli_flag=None, root=root)
            self.assertFalse(enabled)
            self.assertEqual(source, tel.OptOutSource.ENV_VAR)

    def test_env_var_on_overrides_sentinel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            (root / ".weld" / "telemetry.disabled").touch()
            with mock.patch.dict(os.environ, {"WELD_TELEMETRY": "on"}):
                enabled, source = tel.is_enabled(cli_flag=None, root=root)
            self.assertTrue(enabled)
            self.assertEqual(source, tel.OptOutSource.ENV_VAR)

    def test_sentinel_file_disables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            (root / ".weld" / "telemetry.disabled").touch()
            with mock.patch.dict(os.environ, {}, clear=True):
                enabled, source = tel.is_enabled(cli_flag=None, root=root)
            self.assertFalse(enabled)
            self.assertEqual(source, tel.OptOutSource.CONFIG_FILE)

    def test_default_on(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            with mock.patch.dict(os.environ, {}, clear=True):
                enabled, source = tel.is_enabled(cli_flag=None, root=root)
            self.assertTrue(enabled)
            self.assertEqual(source, tel.OptOutSource.DEFAULT_ON)

    def test_recorder_skips_write_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            with _rec(root, cli_flag=False):
                pass
            self.assertFalse((root / ".weld" / tel.TELEMETRY_FILENAME).exists())


class ResolvePathTests(unittest.TestCase):
    def test_workspaces_yaml_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / ".weld").mkdir()
            (ws / ".weld" / "workspaces.yaml").write_text("version: 1\n")
            child = ws / "children" / "a"
            child.mkdir(parents=True)
            (child / ".weld").mkdir()
            (child / ".weld" / "discover.yaml").write_text("# child\n")
            # From the child, resolution must go to the workspace root.
            resolved = tel.resolve_path(child)
            self.assertEqual(resolved, ws / ".weld" / tel.TELEMETRY_FILENAME)

    def test_single_repo_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            sub = root / "weld"
            sub.mkdir()
            resolved = tel.resolve_path(sub)
            self.assertEqual(resolved, root / ".weld" / tel.TELEMETRY_FILENAME)

    def test_xdg_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            # tmp1 has no .weld directory anywhere up the chain.
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp2}):
                resolved = tel.resolve_path(Path(tmp1))
            self.assertEqual(
                resolved, Path(tmp2) / "weld" / tel.TELEMETRY_FILENAME
            )


if __name__ == "__main__":
    unittest.main()
