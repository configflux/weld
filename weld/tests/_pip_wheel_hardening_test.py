"""Unit tests for :mod:`weld.tests._pip_wheel_hardening`.

The helper bundles two defenses around the pip-wheel call in the
wheel-building test boundary:

* **Cache isolation**: yield env vars so each invocation gets a unique
  ``PIP_CACHE_DIR`` and ``PIP_NO_CACHE_DIR=1``. The cross-test pip cache
  was identified as the most likely race seed in the pollution
  investigation.
* **Read-only perimeter**: chmod target files to 0o444 for the duration
  of the with-block, restore to their captured mode on exit. Any future
  producer that reaches back to live source through Bazel's hardlinked
  execroot hits a deterministic ``EACCES`` rather than silently emptying
  the file.

The helper has no Bazel/pip dependency at unit-test time -- the contract
is purely (1) what env keys it yields and (2) that file modes flip and
restore correctly, including on exception paths.
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from weld.tests._pip_wheel_hardening import (  # noqa: E402
    hermetic_pip_wheel,
    pip_wheel_env,
    readonly_perimeter,
)


class PipWheelEnvTest(unittest.TestCase):
    """``pip_wheel_env`` yields the documented env keys + creates the dir."""

    def test_returns_no_cache_dir_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = pip_wheel_env(Path(tmp))
            self.assertEqual(env["PIP_NO_CACHE_DIR"], "1")

    def test_returns_per_invocation_cache_dir_under_tmpdir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env = pip_wheel_env(tmp_path)
            cache_dir = Path(env["PIP_CACHE_DIR"])
            # The cache dir must live under the supplied tmp dir so a
            # crashing test cannot leave the global ~/.cache/pip dirty.
            self.assertEqual(cache_dir.parent, tmp_path)
            self.assertTrue(cache_dir.is_dir())

    def test_distinct_calls_yield_distinct_dirs(self) -> None:
        # Two calls within one TEST_TMPDIR must still get separate dirs
        # so two consecutive pip wheel calls in the same test do not
        # share a cache.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            first = pip_wheel_env(tmp_path)["PIP_CACHE_DIR"]
            second = pip_wheel_env(tmp_path)["PIP_CACHE_DIR"]
            self.assertNotEqual(first, second)


class ReadonlyPerimeterTest(unittest.TestCase):
    """``readonly_perimeter`` chmods to 0o444 and restores on exit."""

    def _make_file(self, root: Path, name: str = "f.py") -> Path:
        path = root / name
        path.write_text("x = 1\n", encoding="utf-8")
        path.chmod(0o644)
        return path

    def test_chmods_target_to_readonly_in_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = self._make_file(Path(tmp))
            with readonly_perimeter([target]):
                mode = stat.S_IMODE(target.stat().st_mode)
                self.assertEqual(mode, 0o444)

    def test_restores_original_mode_on_clean_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = self._make_file(Path(tmp))
            with readonly_perimeter([target]):
                pass
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)

    def test_restores_original_mode_on_exception(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = self._make_file(Path(tmp))
            with self.assertRaises(RuntimeError):
                with readonly_perimeter([target]):
                    raise RuntimeError("boom")
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)

    def test_preserves_pre_existing_non_default_mode(self) -> None:
        # If the caller hands us a 0o600 file (rare but possible), we
        # must restore to 0o600, not flatten to 0o644.
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "secret.py"
            target.write_text("x = 1\n", encoding="utf-8")
            target.chmod(0o600)
            with readonly_perimeter([target]):
                self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o444)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_handles_multiple_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            a = self._make_file(tmp_path, "a.py")
            b = self._make_file(tmp_path, "b.py")
            with readonly_perimeter([a, b]):
                self.assertEqual(stat.S_IMODE(a.stat().st_mode), 0o444)
                self.assertEqual(stat.S_IMODE(b.stat().st_mode), 0o444)
            self.assertEqual(stat.S_IMODE(a.stat().st_mode), 0o644)
            self.assertEqual(stat.S_IMODE(b.stat().st_mode), 0o644)

    def test_empty_target_list_is_a_noop(self) -> None:
        # No targets -> no chmod calls; the with-block must still
        # execute. This lets callers always pass the helper even when
        # the live-source path does not exist (e.g., an installed-pkg
        # test running outside a repo checkout).
        with readonly_perimeter([]):
            pass

    def test_missing_path_is_silently_skipped(self) -> None:
        # The two production consumers protect a path that should
        # always exist (weld/__init__.py), but defensively the helper
        # must not crash on a missing file -- it would mask the real
        # failure with a confusing FileNotFoundError.
        with tempfile.TemporaryDirectory() as tmp:
            ghost = Path(tmp) / "nope.py"
            with readonly_perimeter([ghost]):
                pass


class HermeticPipWheelTest(unittest.TestCase):
    """The combined context manager wires both defenses together."""

    def test_yields_env_dict_and_chmods_during_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target = tmp_path / "f.py"
            target.write_text("x = 1\n", encoding="utf-8")
            target.chmod(0o644)
            with hermetic_pip_wheel(
                test_tmpdir=tmp_path, protect=[target],
            ) as env:
                self.assertEqual(env["PIP_NO_CACHE_DIR"], "1")
                self.assertTrue(Path(env["PIP_CACHE_DIR"]).is_dir())
                self.assertEqual(
                    stat.S_IMODE(target.stat().st_mode), 0o444,
                )
            # Mode restored after the block.
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)

    def test_chmod_restored_when_subprocess_call_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target = tmp_path / "f.py"
            target.write_text("x = 1\n", encoding="utf-8")
            target.chmod(0o644)
            with self.assertRaises(ValueError):
                with hermetic_pip_wheel(
                    test_tmpdir=tmp_path, protect=[target],
                ):
                    raise ValueError("subprocess blew up")
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)

    def test_protect_defaults_to_empty(self) -> None:
        # Approach-A-only usage: callers that just want cache isolation
        # (no read-only perimeter) can omit the protect kwarg.
        with tempfile.TemporaryDirectory() as tmp:
            with hermetic_pip_wheel(test_tmpdir=Path(tmp)) as env:
                self.assertEqual(env["PIP_NO_CACHE_DIR"], "1")

    def test_does_not_leak_into_os_environ(self) -> None:
        # The helper must hand the env dict to the caller, never mutate
        # process-global os.environ -- otherwise a flaky test could
        # leave PIP_CACHE_DIR set on a no-longer-existing dir.
        before = os.environ.get("PIP_CACHE_DIR")
        with tempfile.TemporaryDirectory() as tmp:
            with hermetic_pip_wheel(test_tmpdir=Path(tmp)):
                pass
        after = os.environ.get("PIP_CACHE_DIR")
        self.assertEqual(before, after)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
