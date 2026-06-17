"""Unit tests for :mod:`weld.tests._pip_wheel_hardening`.

The helper bundles two defenses around the pip-wheel call in the
wheel-building test boundary:

* **Cache isolation**: yield env vars so each invocation gets a unique
  ``PIP_CACHE_DIR`` and ``PIP_NO_CACHE_DIR=1``. The cross-test pip cache
  was identified as the most likely race seed in the pollution
  investigation.
* **Content perimeter**: snapshot each target file's bytes for the
  duration of the with-block; on exit, if the bytes changed, restore
  them and raise ``SourcePerimeterViolation``. Any producer that reaches
  back to live source through Bazel's hardlinked execroot and rewrites
  the file is caught loudly and the live tree is self-healed. The
  perimeter never touches file *mode* -- the historical chmod-to-0o444
  approach leaked the read-only bit onto the shared runfiles inode and
  raced concurrent sandbox input-copies into EACCES (bd ck8l / 5ko1).

The helper has no Bazel/pip dependency at unit-test time -- the contract
is purely (1) what env keys it yields and (2) that protected files'
bytes are verified, restored on mutation, and never have their mode
perturbed -- including on exception paths.
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
    SourcePerimeterViolation,
    content_perimeter,
    hermetic_pip_wheel,
    pip_wheel_env,
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


class ContentPerimeterTest(unittest.TestCase):
    """``content_perimeter`` snapshots bytes, verifies + heals on exit.

    The perimeter must (a) leave bytes untouched when the body does not
    write, (b) restore + raise loudly when the body mutates a protected
    file, and -- the bd ck8l / 5ko1 regression bar -- (c) never change
    the file *mode*, including on a hardlinked inode.
    """

    def _make_file(self, root: Path, name: str = "f.py") -> Path:
        path = root / name
        path.write_text("x = 1\n", encoding="utf-8")
        path.chmod(0o644)
        return path

    def test_unchanged_file_passes_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = self._make_file(Path(tmp))
            with content_perimeter([target]):
                pass
            self.assertEqual(target.read_text(encoding="utf-8"), "x = 1\n")

    def test_does_not_change_mode_in_block_or_after(self) -> None:
        # Core regression for bd ck8l / 5ko1: the perimeter must NOT
        # touch file mode -- chmod on a Bazel-hardlinked source inode
        # leaked the read-only bit onto the runfiles copy a concurrent
        # sandbox had to read.
        with tempfile.TemporaryDirectory() as tmp:
            target = self._make_file(Path(tmp))
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)
            with content_perimeter([target]):
                self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)

    def test_hardlinked_inode_mode_never_flips(self) -> None:
        # Simulate Bazel's runfiles/execroot model exactly: the live
        # source file and the copy the sandbox runner reads are two
        # paths sharing one inode (a hardlink). The perimeter must leave
        # the linked copy's mode at 0o644 throughout, so a concurrently
        # starting sandboxed test can always copy it in.
        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / "live" / "__init__.py"
            live.parent.mkdir()
            live.touch()
            live.chmod(0o644)
            runfiles = Path(tmp) / "runfiles" / "__init__.py"
            runfiles.parent.mkdir()
            os.link(live, runfiles)  # shared inode, as Bazel does
            with content_perimeter([live]):
                self.assertEqual(
                    stat.S_IMODE(runfiles.stat().st_mode), 0o644,
                    "perimeter leaked a mode change onto the hardlinked "
                    "runfiles copy -- the bd ck8l / 5ko1 EACCES race",
                )
            self.assertEqual(stat.S_IMODE(runfiles.stat().st_mode), 0o644)

    def test_mutation_is_restored_and_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = self._make_file(Path(tmp))
            with self.assertRaises(SourcePerimeterViolation):
                with content_perimeter([target]):
                    # A rogue producer empties the protected file --
                    # exactly the source-pollution regression class.
                    target.write_text("", encoding="utf-8")
            # The live tree is self-healed back to the snapshot.
            self.assertEqual(target.read_text(encoding="utf-8"), "x = 1\n")

    def test_deletion_is_restored_and_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = self._make_file(Path(tmp))
            with self.assertRaises(SourcePerimeterViolation):
                with content_perimeter([target]):
                    target.unlink()
            self.assertEqual(target.read_text(encoding="utf-8"), "x = 1\n")

    def test_body_exception_propagates_and_does_not_mask(self) -> None:
        # An unrelated error inside the body must surface unchanged when
        # the protected file was not touched -- the perimeter only
        # raises its own violation when bytes actually changed.
        with tempfile.TemporaryDirectory() as tmp:
            target = self._make_file(Path(tmp))
            with self.assertRaises(RuntimeError):
                with content_perimeter([target]):
                    raise RuntimeError("boom")
            self.assertEqual(target.read_text(encoding="utf-8"), "x = 1\n")

    def test_handles_multiple_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            a = self._make_file(tmp_path, "a.py")
            b = self._make_file(tmp_path, "b.py")
            with self.assertRaises(SourcePerimeterViolation):
                with content_perimeter([a, b]):
                    b.write_text("tampered\n", encoding="utf-8")
            # Both untouched-a and tampered-b end at their snapshots.
            self.assertEqual(a.read_text(encoding="utf-8"), "x = 1\n")
            self.assertEqual(b.read_text(encoding="utf-8"), "x = 1\n")

    def test_empty_target_list_is_a_noop(self) -> None:
        # No targets -> nothing snapshotted; the with-block still runs.
        # This lets callers always pass the helper even when the
        # live-source path does not exist (installed-pkg test outside a
        # repo checkout).
        with content_perimeter([]):
            pass

    def test_missing_path_is_silently_skipped(self) -> None:
        # The production consumers protect a path that should always
        # exist (weld/__init__.py), but defensively the helper must not
        # crash on a missing file -- it would mask the real failure with
        # a confusing FileNotFoundError.
        with tempfile.TemporaryDirectory() as tmp:
            ghost = Path(tmp) / "nope.py"
            with content_perimeter([ghost]):
                pass


class HermeticPipWheelTest(unittest.TestCase):
    """The combined context manager wires both defenses together."""

    def test_yields_env_dict_and_protects_bytes_without_mode_change(
        self,
    ) -> None:
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
                # Mode is NOT touched (the bd ck8l / 5ko1 fix) and the
                # bytes are readable throughout.
                self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)
                self.assertEqual(target.read_text(encoding="utf-8"), "x = 1\n")
            # Unchanged after the block; mode still 0o644.
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)
            self.assertEqual(target.read_text(encoding="utf-8"), "x = 1\n")

    def test_body_exception_propagates_when_bytes_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target = tmp_path / "f.py"
            target.write_text("x = 1\n", encoding="utf-8")
            target.chmod(0o644)
            # The body raises without touching the protected file -- the
            # original error must propagate, not be masked by the
            # perimeter, and mode must be untouched.
            with self.assertRaises(ValueError):
                with hermetic_pip_wheel(
                    test_tmpdir=tmp_path, protect=[target],
                ):
                    raise ValueError("subprocess blew up")
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)
            self.assertEqual(target.read_text(encoding="utf-8"), "x = 1\n")

    def test_byte_mutation_through_combined_helper_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target = tmp_path / "f.py"
            target.write_text("x = 1\n", encoding="utf-8")
            target.chmod(0o644)
            with self.assertRaises(SourcePerimeterViolation):
                with hermetic_pip_wheel(
                    test_tmpdir=tmp_path, protect=[target],
                ):
                    target.write_text("", encoding="utf-8")
            # Self-healed back to the snapshot.
            self.assertEqual(target.read_text(encoding="utf-8"), "x = 1\n")

    def test_protect_defaults_to_empty(self) -> None:
        # Approach-A-only usage: callers that just want cache isolation
        # (no content perimeter) can omit the protect kwarg.
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
