"""First-run stderr notice tests for the CLI Recorder wrapping (T3).

Per ADR 0035 § "First-run Notice", the very first telemetry event for a
resolved file path emits one line on stderr announcing local telemetry
is on and listing the three opt-out mechanisms. Subsequent invocations
stay silent. After ``wd telemetry clear --yes`` the file is gone, so
the next event re-prints -- a deliberate UX choice so a wipe always
yields a fresh confirmation.

These tests run :func:`weld.cli.main` directly (no subprocess) against
a tempdir-backed project and assert on captured stderr, mirroring the
patterns in ``weld_telemetry_cli_test.py`` and ``weld_brief_cli_test.py``.
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
def _isolated_env(root: Path):
    """Capture stdout/stderr; clear WELD_TELEMETRY; chdir into ``root``."""
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    env_patch = mock.patch.dict(
        os.environ, {"XDG_STATE_HOME": str(root / "_xdg")}, clear=False
    )
    with _chdir(root), env_patch, \
         mock.patch.object(sys, "stdout", out_buf), \
         mock.patch.object(sys, "stderr", err_buf):
        # Drop any inherited WELD_TELEMETRY value so we test default-on.
        os.environ.pop("WELD_TELEMETRY", None)
        yield out_buf, err_buf


class FirstRunNoticeTests(unittest.TestCase):
    """ADR 0035 § "First-run Notice"."""

    def test_first_invocation_prints_notice_on_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            with _isolated_env(root) as (_out, err):
                rc = cli_main(["--version"])
            self.assertEqual(rc, 0)
            self.assertIn("local telemetry on", err.getvalue())
            self.assertIn("WELD_TELEMETRY=off", err.getvalue())
            self.assertIn("--no-telemetry", err.getvalue())
            # The file now exists; recorded one event.
            self.assertTrue(
                (root / ".weld" / tel.TELEMETRY_FILENAME).is_file()
            )

    def test_second_invocation_is_silent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            # First run primes the file so the notice fires once.
            with _isolated_env(root):
                cli_main(["--version"])
            # Second run: notice must NOT print again.
            with _isolated_env(root) as (_out, err):
                rc = cli_main(["--version"])
            self.assertEqual(rc, 0)
            self.assertNotIn("local telemetry on", err.getvalue())

    def test_clear_removes_file_and_next_run_reprints_notice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            telem_path = root / ".weld" / tel.TELEMETRY_FILENAME

            # First run primes the file.
            with _isolated_env(root):
                cli_main(["--version"])
            self.assertTrue(telem_path.is_file())

            # ``wd telemetry clear --yes`` deletes the file.
            with _isolated_env(root):
                rc_clear = cli_main(["telemetry", "clear", "--yes"])
            self.assertEqual(rc_clear, 0)
            self.assertFalse(telem_path.exists())

            # Next event must re-print the notice.
            with _isolated_env(root) as (_out, err):
                rc = cli_main(["--version"])
            self.assertEqual(rc, 0)
            self.assertIn("local telemetry on", err.getvalue())
            self.assertTrue(telem_path.is_file())


if __name__ == "__main__":
    unittest.main()
