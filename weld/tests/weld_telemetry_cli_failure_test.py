"""Failure-isolation tests for the CLI Recorder wrapping (T3).

ADR 0035 § "Failure-isolated writer" mandates that telemetry writer
failures NEVER alter the wrapped command's behavior. These tests
monkey-patch :func:`weld._telemetry._write_locked` to raise and verify:

- the inner command's return value is preserved exactly,
- no exception escapes :func:`weld.cli.main`,
- ``BrokenPipeError`` continues to map to exit code 141 even when the
  recorder's own write would have failed.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Make ``weld`` importable from a Bazel runfiles tree as well as from the
# repo root in local runs.
_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)
_tests_dir = str(Path(__file__).resolve().parent)
if _tests_dir not in sys.path:
    sys.path.insert(0, _tests_dir)

from weld import _telemetry as tel  # noqa: E402
from weld import cli  # noqa: E402
from weld.cli import main as cli_main  # noqa: E402
from telemetry_test_helpers import (  # noqa: E402
    captured as _captured,
    make_repo as _make_repo,
)


class WriterFailureIsolationTests(unittest.TestCase):
    """ADR 0035 § "Failure-isolated writer"."""

    def test_oserror_in_writer_does_not_break_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            with _captured(root), \
                 mock.patch.object(
                     tel, "_write_locked",
                     side_effect=OSError("disk full"),
                 ):
                rc = cli_main(["--version"])
            # Wrapped command still returns its real exit code.
            self.assertEqual(rc, 0)
            # File never created since the writer raised.
            self.assertFalse(
                (root / ".weld" / tel.TELEMETRY_FILENAME).exists()
            )

    def test_runtime_error_in_writer_does_not_break_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))
            with _captured(root), \
                 mock.patch.object(
                     tel, "_write_locked",
                     side_effect=RuntimeError("boom"),
                 ):
                rc = cli_main(["--version"])
            self.assertEqual(rc, 0)

    def test_inner_dispatch_return_value_preserved(self) -> None:
        """When the writer raises, the wrapped ``_dispatch`` rc must survive."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))

            def _fake_dispatch(_argv):
                return 7  # arbitrary non-zero, non-conventional exit code

            with _captured(root), \
                 mock.patch.object(cli, "_dispatch", side_effect=_fake_dispatch), \
                 mock.patch.object(
                     tel, "_write_locked",
                     side_effect=OSError("nope"),
                 ):
                rc = cli_main(["query", "foo"])
            self.assertEqual(rc, 7)


class BrokenPipePreservedTests(unittest.TestCase):
    """ADR 0035 + the existing CLI contract: pipe close still exits 141."""

    def test_broken_pipe_returns_141_with_telemetry_off(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))

            def _fake_dispatch(_argv):
                raise BrokenPipeError()

            with _captured(root), \
                 mock.patch.object(cli, "_dispatch", side_effect=_fake_dispatch), \
                 mock.patch.dict(os.environ, {"WELD_TELEMETRY": "off"}):
                rc = cli_main(["--version"])
            self.assertEqual(rc, 141)

    def test_broken_pipe_returns_141_when_writer_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo(Path(tmp))

            def _fake_dispatch(_argv):
                raise BrokenPipeError()

            with _captured(root), \
                 mock.patch.object(cli, "_dispatch", side_effect=_fake_dispatch), \
                 mock.patch.object(
                     tel, "_write_locked",
                     side_effect=OSError("ignored"),
                 ):
                rc = cli_main(["--version"])
            self.assertEqual(rc, 141)


if __name__ == "__main__":
    unittest.main()
