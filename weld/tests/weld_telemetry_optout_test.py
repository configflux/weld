"""Opt-out resolution-order tests for the CLI Recorder wrapping (T3).

Per ADR 0035 § "Default-on with three-tier opt-out" the resolution
order, top wins, is:

1. ``--no-telemetry`` CLI flag (stripped from argv before dispatch).
2. ``WELD_TELEMETRY=off|0|false|no|disabled`` env var.
3. ``.weld/telemetry.disabled`` sentinel file.
4. Default: on.

These tests assert all four tiers via direct :func:`weld.cli.main`
invocation. They also verify the argv-strip contract: the inner
subcommand parser must NEVER see ``--no-telemetry``.
"""

from __future__ import annotations

import io
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
from weld import cli  # noqa: E402
from weld.cli import main as cli_main  # noqa: E402


def _make_repo(root: Path) -> Path:
    """Create a minimal single-repo project so resolve_path() finds it."""
    weld = root / ".weld"
    weld.mkdir(parents=True, exist_ok=True)
    (weld / "discover.yaml").write_text("# placeholder\n")
    return root


@contextmanager
def _chdir(target: Path):
    prev = Path.cwd()
    os.chdir(target)
    try:
        yield
    finally:
        os.chdir(prev)


@contextmanager
def _captured(root: Path, env: dict[str, str] | None = None):
    """Run with isolated cwd, env (XDG redirected), captured streams."""
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    base_env = {"XDG_STATE_HOME": str(root / "_xdg")}
    if env:
        base_env.update(env)
    with _chdir(root), \
         mock.patch.dict(os.environ, base_env, clear=False), \
         mock.patch.object(sys, "stdout", out_buf), \
         mock.patch.object(sys, "stderr", err_buf):
        # Strip any inherited WELD_TELEMETRY unless the caller set it.
        if env is None or "WELD_TELEMETRY" not in env:
            os.environ.pop("WELD_TELEMETRY", None)
        yield out_buf, err_buf


def _events(root: Path) -> list[str]:
    path = root / ".weld" / tel.TELEMETRY_FILENAME
    if not path.exists():
        return []
    return [ln for ln in path.read_text().splitlines() if ln.strip()]


class CliFlagWinsTests(unittest.TestCase):
    """ADR 0035: ``--no-telemetry`` CLI flag overrides every other tier."""

    def test_flag_wins_over_env_on_and_sentinel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            (root / ".weld" / "telemetry.disabled").touch()
            env = {"WELD_TELEMETRY": "on"}
            with _captured(root, env=env):
                rc = cli_main(["--no-telemetry", "--version"])
            self.assertEqual(rc, 0)
            # No event recorded -- file never created.
            self.assertFalse(
                (root / ".weld" / tel.TELEMETRY_FILENAME).exists()
            )

    def test_flag_stripped_from_argv_before_dispatch(self) -> None:
        """Inner ``_dispatch`` must NEVER see ``--no-telemetry``."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            seen: list[list[str] | None] = []

            def _capture_dispatch(argv: list[str] | None) -> int:
                seen.append(list(argv) if argv is not None else None)
                # Mimic the version branch; return 0.
                return 0

            with _captured(root), \
                 mock.patch.object(cli, "_dispatch", side_effect=_capture_dispatch):
                cli_main(["--no-telemetry", "query", "foo", "--json"])

            self.assertEqual(len(seen), 1)
            argv_seen = seen[0] or []
            self.assertNotIn("--no-telemetry", argv_seen)
            # Other tokens must survive untouched.
            self.assertEqual(argv_seen, ["query", "foo", "--json"])


class EnvOffTests(unittest.TestCase):
    """ADR 0035: ``WELD_TELEMETRY=off`` disables recording."""

    def test_env_off_creates_no_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            with _captured(root, env={"WELD_TELEMETRY": "off"}):
                rc = cli_main(["--version"])
            self.assertEqual(rc, 0)
            self.assertFalse(
                (root / ".weld" / tel.TELEMETRY_FILENAME).exists()
            )

    def test_env_off_across_many_invocations_creates_no_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            with _captured(root, env={"WELD_TELEMETRY": "off"}):
                for _ in range(5):
                    cli_main(["--version"])
            self.assertFalse(
                (root / ".weld" / tel.TELEMETRY_FILENAME).exists()
            )


class SentinelFileTests(unittest.TestCase):
    """ADR 0035: ``.weld/telemetry.disabled`` sentinel disables recording."""

    def test_sentinel_disables_with_no_flag_no_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            (root / ".weld" / "telemetry.disabled").touch()
            with _captured(root):
                rc = cli_main(["--version"])
            self.assertEqual(rc, 0)
            self.assertFalse(
                (root / ".weld" / tel.TELEMETRY_FILENAME).exists()
            )


class DefaultOnTests(unittest.TestCase):
    """ADR 0035: default-on baseline."""

    def test_default_on_records_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            with _captured(root):
                rc = cli_main(["--version"])
            self.assertEqual(rc, 0)
            evs = _events(root)
            self.assertEqual(len(evs), 1)
            self.assertIn('"command": "version"', evs[0])

    def test_help_command_recorded_as_help(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            with _captured(root):
                rc = cli_main([])
            self.assertEqual(rc, 0)
            evs = _events(root)
            self.assertEqual(len(evs), 1)
            self.assertIn('"command": "help"', evs[0])


class CommandResolutionTests(unittest.TestCase):
    """The ``command`` field reflects the resolved subcommand or sentinel."""

    def test_dash_h_records_help(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            with _captured(root):
                cli_main(["-h"])
            evs = _events(root)
            self.assertIn('"command": "help"', evs[0])

    def test_short_version_records_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            with _captured(root):
                cli_main(["-V"])
            evs = _events(root)
            self.assertIn('"command": "version"', evs[0])

    def test_unknown_subcommand_records_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            # An obviously-not-a-real subcommand. The CLI's fallback path
            # routes anything unknown through ``graph_mod.main``, which
            # may exit non-zero -- we only care about the recorded
            # ``command`` field, not the rc.
            with _captured(root):
                try:
                    cli_main(["nonsense-not-a-real-command"])
                except SystemExit:
                    pass
                except BaseException:
                    pass
            evs = _events(root)
            self.assertTrue(evs, "expected at least one event")
            self.assertIn('"command": "unknown"', evs[0])


if __name__ == "__main__":
    unittest.main()
