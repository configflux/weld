"""Setup-hook tests for the libclang variant of the public benchmark.

A C++ corpus entry can declare a ``setup:`` clause -- a one-shot
post-clone command (typically ``cmake -B build``) that produces a
``compile_commands.json``. The libclang adapter (the C++ best-in-class
methodology, see the relevant ADR) only runs when that database exists.

The setup step is *gated* by a binary check (e.g. ``cmake --version``).
When the gate fails -- because the binary is missing in the runtime
environment -- the corpus repo materializes successfully (clone still
worked) but the per-repo ``setup_status`` is recorded as
``setup_unavailable`` so the libclang adapter can later emit SKIPPED
rows with a stable reason.

These tests use a fake subprocess so they run hermetically in CI.
Materialize-level integration with the per-repo status map lives in
the sibling ``weld_public_bench_libclang_materialize_test.py``; this
file owns the low-level ``run_setup_step`` behavior and the dataclass
shape.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_repo_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.bench._public_corpus import SetupStep  # noqa: E402
from weld.bench._public_setup import (  # noqa: E402
    SETUP_BINARY_MISSING_REASON,
    run_setup_step,
)


class SetupStepShapeTest(unittest.TestCase):
    """The setup dataclass carries the gate binary, command, and output."""

    def test_setup_step_has_required_fields(self) -> None:
        step = SetupStep(
            requires_binary="cmake",
            cmd=("cmake", "-B", "build", "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON", "."),
            produces="build/compile_commands.json",
            timeout_s=120,
        )
        self.assertEqual(step.requires_binary, "cmake")
        self.assertIn("cmake", step.cmd)
        self.assertIn("DCMAKE_EXPORT_COMPILE_COMMANDS=ON", " ".join(step.cmd))
        self.assertTrue(step.produces.endswith("compile_commands.json"))
        self.assertEqual(step.timeout_s, 120)


class RunSetupStepGateTest(unittest.TestCase):
    """``run_setup_step`` checks the gate binary before invoking the cmd."""

    def test_gate_binary_missing_returns_unavailable(self) -> None:
        step = SetupStep(
            requires_binary="cmake",
            cmd=("cmake", "-B", "build"),
            produces="build/compile_commands.json",
            timeout_s=60,
        )
        with tempfile.TemporaryDirectory() as repo_root:
            # No cmake on PATH.
            with patch(
                "weld.bench._public_setup._which",
                return_value=None,
            ) as mock_which, patch(
                "weld.bench._public_setup._run_setup_subprocess",
            ) as mock_run:
                status, reason = run_setup_step(step, Path(repo_root))
            mock_which.assert_called_once_with("cmake")
            mock_run.assert_not_called()
            self.assertEqual(status, "setup_unavailable")
            self.assertIn("cmake", reason)
            self.assertEqual(
                reason, SETUP_BINARY_MISSING_REASON.format(binary="cmake"),
            )

    def test_gate_binary_available_runs_cmd(self) -> None:
        step = SetupStep(
            requires_binary="cmake",
            cmd=("cmake", "-B", "build"),
            produces="build/compile_commands.json",
            timeout_s=60,
        )
        with tempfile.TemporaryDirectory() as repo_root:
            root_p = Path(repo_root)
            # Touch the produces file so the post-run check passes.
            (root_p / "build").mkdir()
            (root_p / "build" / "compile_commands.json").write_text(
                "[]", encoding="utf-8",
            )

            def _fake_run(cmd, cwd, timeout):  # noqa: ARG001
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout="", stderr="",
                )

            with patch(
                "weld.bench._public_setup._which",
                return_value="/usr/bin/cmake",
            ), patch(
                "weld.bench._public_setup._run_setup_subprocess",
                side_effect=_fake_run,
            ) as mock_run:
                status, reason = run_setup_step(step, root_p)
            mock_run.assert_called_once()
            self.assertEqual(status, "setup_ok")
            self.assertEqual(reason, "")

    def test_cmd_non_zero_exit_returns_setup_failed(self) -> None:
        step = SetupStep(
            requires_binary="cmake",
            cmd=("cmake", "-B", "build"),
            produces="build/compile_commands.json",
            timeout_s=60,
        )
        with tempfile.TemporaryDirectory() as repo_root:
            def _fake_run(cmd, cwd, timeout):  # noqa: ARG001
                return subprocess.CompletedProcess(
                    args=cmd, returncode=1, stdout="", stderr="cmake error",
                )

            with patch(
                "weld.bench._public_setup._which",
                return_value="/usr/bin/cmake",
            ), patch(
                "weld.bench._public_setup._run_setup_subprocess",
                side_effect=_fake_run,
            ):
                status, reason = run_setup_step(step, Path(repo_root))
            self.assertEqual(status, "setup_failed")
            self.assertIn("setup", reason.lower())

    def test_cmd_timeout_returns_setup_failed(self) -> None:
        step = SetupStep(
            requires_binary="cmake",
            cmd=("cmake", "-B", "build"),
            produces="build/compile_commands.json",
            timeout_s=1,
        )
        with tempfile.TemporaryDirectory() as repo_root:
            def _boom(*args, **kw):  # noqa: ARG001
                raise subprocess.TimeoutExpired(cmd=["cmake"], timeout=1)

            with patch(
                "weld.bench._public_setup._which",
                return_value="/usr/bin/cmake",
            ), patch(
                "weld.bench._public_setup._run_setup_subprocess",
                side_effect=_boom,
            ):
                status, reason = run_setup_step(step, Path(repo_root))
            self.assertEqual(status, "setup_failed")
            self.assertIn("timed out", reason.lower())

    def test_produces_file_missing_returns_setup_failed(self) -> None:
        # cmake exits 0 but compile_commands.json never appears.
        step = SetupStep(
            requires_binary="cmake",
            cmd=("cmake", "-B", "build"),
            produces="build/compile_commands.json",
            timeout_s=60,
        )
        with tempfile.TemporaryDirectory() as repo_root:
            def _fake_run(cmd, cwd, timeout):  # noqa: ARG001
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout="", stderr="",
                )

            with patch(
                "weld.bench._public_setup._which",
                return_value="/usr/bin/cmake",
            ), patch(
                "weld.bench._public_setup._run_setup_subprocess",
                side_effect=_fake_run,
            ):
                status, reason = run_setup_step(step, Path(repo_root))
            self.assertEqual(status, "setup_failed")
            self.assertIn("compile_commands.json", reason)


if __name__ == "__main__":
    unittest.main()
