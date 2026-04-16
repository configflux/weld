"""Subprocess locale determinism contract test.

Covers ADR 0012 §2 row 3. ``weld._git.get_git_sha``, ``is_git_repo``,
and ``commits_behind`` must all call ``subprocess.run`` with
``env={..., 'LC_ALL': 'C'}``. The contract mandates locale
neutralization on every subprocess invocation.

This test verifies the contract at the *call-site* level. Rather than
trying to trigger a locale-dependent reordering (which requires a
specific non-English locale installed on the test host -- not
portable), the test monkeypatches ``subprocess.run`` inside the
``weld._git`` module, captures the ``env`` kwarg the helper passes,
and asserts ``LC_ALL=C`` is in that env.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld import _git as git_helpers  # noqa: E402


# Capture the real subprocess.run before any patching so the capturing
# shim can delegate to it without recursing back into itself when
# ``subprocess.run`` has been monkeypatched.
_REAL_SUBPROCESS_RUN = subprocess.run


class _CapturingRun:
    """Stand-in for ``subprocess.run`` that records ``env=`` values.

    The real ``subprocess.run`` is still invoked afterward so the
    helper under test behaves normally -- only the ``env`` argument
    gets recorded for later inspection.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append({"args": args, "kwargs": dict(kwargs)})
        return _REAL_SUBPROCESS_RUN(*args, **kwargs)


class SubprocessLocaleDeterminismTest(unittest.TestCase):
    """ADR 0012 §2 row 3: every subprocess call must pass ``LC_ALL=C``."""

    def _assert_lc_all_c(self, env) -> None:
        self.assertIsNotNone(
            env,
            "subprocess.run must be invoked with env= set. "
            "Fix: env={**os.environ, 'LC_ALL': 'C'} at every call "
            "site (ADR 0012 §2 row 3).",
        )
        self.assertEqual(
            env.get("LC_ALL"),
            "C",
            "subprocess.run env must include LC_ALL=C "
            "(ADR 0012 §2 row 3). "
            "Got env.LC_ALL=%r." % env.get("LC_ALL"),
        )

    def test_get_git_sha_passes_lc_all_c(self) -> None:
        """weld._git.get_git_sha must invoke git with LC_ALL=C."""
        with tempfile.TemporaryDirectory(prefix="locale-det-") as td:
            root = Path(td)
            subprocess.run(
                ["git", "init", "--quiet"], cwd=str(root), check=False
            )
            subprocess.run(
                ["git", "-C", str(root), "config", "user.email", "t@x"],
                check=False,
            )
            subprocess.run(
                ["git", "-C", str(root), "config", "user.name", "T"],
                check=False,
            )
            (root / "f").write_text("x\n", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(root), "add", "f"], check=False
            )
            subprocess.run(
                [
                    "git", "-C", str(root),
                    "-c", "commit.gpgsign=false",
                    "commit", "-q", "-m", "c",
                ],
                check=False,
            )

            capturing = _CapturingRun()
            with patch.object(subprocess, "run", capturing):
                git_helpers.get_git_sha(root)

        self.assertTrue(
            capturing.calls,
            "Expected at least one subprocess.run invocation from "
            "get_git_sha; got none.",
        )
        self._assert_lc_all_c(capturing.calls[0]["kwargs"].get("env"))

    def test_is_git_repo_passes_lc_all_c(self) -> None:
        """is_git_repo must invoke git with LC_ALL=C for the same reason."""
        with tempfile.TemporaryDirectory(prefix="locale-det-") as td:
            root = Path(td)
            subprocess.run(
                ["git", "init", "--quiet"], cwd=str(root), check=False
            )
            capturing = _CapturingRun()
            with patch.object(subprocess, "run", capturing):
                git_helpers.is_git_repo(root)

        self.assertTrue(capturing.calls, "Expected at least one git call.")
        self._assert_lc_all_c(capturing.calls[0]["kwargs"].get("env"))

    def test_commits_behind_passes_lc_all_c(self) -> None:
        """commits_behind must invoke git with LC_ALL=C."""
        with tempfile.TemporaryDirectory(prefix="locale-det-") as td:
            root = Path(td)
            subprocess.run(
                ["git", "init", "--quiet"], cwd=str(root), check=False
            )
            capturing = _CapturingRun()
            with patch.object(subprocess, "run", capturing):
                git_helpers.commits_behind(root, "HEAD", "HEAD")

        self.assertTrue(capturing.calls, "Expected at least one git call.")
        self._assert_lc_all_c(capturing.calls[0]["kwargs"].get("env"))


if __name__ == "__main__":
    unittest.main()
