"""Unit tests for :mod:`weld.tests._source_tree_copy`.

The helper is the single shared implementation of "give me a hermetic copy
of the weld package source for pip-wheel smoke tests" (see bd 6h8b). The
two existing consumers -- :mod:`weld_mcp_install_smoke_test` and
:mod:`weld_source_pollution_guard_test` -- exercise the integration path.
This file pins the helper's contract in isolation:

* Default allowlist (``allowlist is None``) literally is
  ``("weld", "pyproject.toml", ".weld")`` per the planning brief; ``.weld``
  is optional and silently skipped when absent.
* Explicit allowlist copies only the named children (positive case).
* Directory entries copy recursively (``shutil.copytree``).
* File entries copy bytes + metadata (``shutil.copy2``).
* Names not present under ``src`` are silently skipped -- this is what
  lets the default ``.weld`` entry be conditional without a sentinel.
* ``shutil.ignore_patterns`` is deliberately NOT used (the whole point of
  switching to an allowlist is to bound disk footprint at copy time).
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from weld.tests._source_tree_copy import (  # noqa: E402
    DEFAULT_ALLOWLIST,
    copy_weld_source,
    wheel_build_allowlist,
)


def _make_tree(root: Path) -> None:
    """Build a small fixture tree under *root* that exercises every path
    the helper has to handle: a directory entry, a file entry, an
    unrelated entry that must NOT be copied without an explicit listing,
    and a nested file inside the directory entry."""
    (root / "weld").mkdir()
    (root / "weld" / "__init__.py").write_text("# weld pkg\n", encoding="utf-8")
    (root / "weld" / "core.py").write_text("VERSION = '0'\n", encoding="utf-8")
    (root / "weld" / "nested").mkdir()
    (root / "weld" / "nested" / "deep.py").write_text("# nested\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        "[project]\nname='configflux-weld'\n", encoding="utf-8"
    )
    # Heavy unrelated payload that must stay out of the copy unless the
    # caller asks for it explicitly. This pins the allowlist contract.
    (root / "tests").mkdir()
    (root / "tests" / "huge_fixture.bin").write_text("X" * 100, encoding="utf-8")


class CopyWeldSourceContractTest(unittest.TestCase):
    """Behavior pins for :func:`copy_weld_source`."""

    def test_default_allowlist_literal(self) -> None:
        # Pin the literal tuple so a future change is a visible diff.
        self.assertEqual(
            DEFAULT_ALLOWLIST, ("weld", "pyproject.toml", ".weld")
        )

    def test_default_allowlist_copies_listed_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "src"
            src.mkdir()
            _make_tree(src)
            dest = tmp_path / "dest"

            copy_weld_source(src, dest)

            # Directory and file from the default list arrived.
            self.assertTrue((dest / "weld" / "__init__.py").is_file())
            self.assertTrue((dest / "weld" / "nested" / "deep.py").is_file())
            self.assertTrue((dest / "pyproject.toml").is_file())
            # Allowlist semantics: anything not named must NOT appear.
            self.assertFalse(
                (dest / "tests").exists(),
                "default allowlist leaked an unnamed child into the copy",
            )

    def test_missing_optional_entry_is_silently_skipped(self) -> None:
        # ``.weld`` is the canonical optional entry in the default list;
        # absence must not raise so the helper stays drop-in for repos
        # that have not run ``wd init``.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "src"
            src.mkdir()
            _make_tree(src)
            self.assertFalse((src / ".weld").exists())
            dest = tmp_path / "dest"

            copy_weld_source(src, dest)  # default list includes .weld

            # No exception, no spurious .weld in dest.
            self.assertFalse((dest / ".weld").exists())

    def test_explicit_allowlist_overrides_default(self) -> None:
        # Caller passes a tighter list -- only the requested entries land
        # in dest. This is the per-test override path that the two real
        # consumers use to add their own larger lists.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "src"
            src.mkdir()
            _make_tree(src)
            dest = tmp_path / "dest"

            copy_weld_source(src, dest, allowlist=["pyproject.toml"])

            self.assertTrue((dest / "pyproject.toml").is_file())
            # ``weld`` was in the default but was NOT in the explicit
            # allowlist; must not be copied.
            self.assertFalse((dest / "weld").exists())
            self.assertFalse((dest / "tests").exists())

    def test_file_entry_uses_copy2_preserving_metadata(self) -> None:
        # ``copy2`` (not ``copy``) is the contract -- mtime survives so
        # downstream tooling that hashes or sorts by mtime keeps working.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "src"
            src.mkdir()
            _make_tree(src)
            # Pin a known mtime on the source file.
            src_file = src / "pyproject.toml"
            import os
            os.utime(src_file, (1_700_000_000, 1_700_000_000))
            dest = tmp_path / "dest"

            copy_weld_source(src, dest, allowlist=["pyproject.toml"])

            dest_file = dest / "pyproject.toml"
            self.assertTrue(dest_file.is_file())
            self.assertEqual(
                src_file.read_bytes(), dest_file.read_bytes(),
                "bytes diverged between src and dest",
            )
            self.assertEqual(
                int(dest_file.stat().st_mtime), 1_700_000_000,
                "copy2 should preserve mtime; got "
                f"{dest_file.stat().st_mtime!r}",
            )

    def test_dest_is_created_if_missing(self) -> None:
        # The helper must not require the caller to mkdir() dest first.
        # Both production call sites pass a fresh tmp subpath.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "src"
            src.mkdir()
            _make_tree(src)
            dest = tmp_path / "fresh-dest-does-not-exist-yet"
            self.assertFalse(dest.exists())

            copy_weld_source(src, dest, allowlist=["pyproject.toml"])

            self.assertTrue(dest.is_dir())
            self.assertTrue((dest / "pyproject.toml").is_file())

    def test_unknown_entry_is_silently_skipped(self) -> None:
        # Same contract as the missing optional entry, but for an
        # explicit allowlist with a typo / stale name. The helper must
        # not raise so callers can pass union-of-needed lists without
        # branching on filesystem state.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "src"
            src.mkdir()
            _make_tree(src)
            dest = tmp_path / "dest"

            copy_weld_source(
                src, dest,
                allowlist=["pyproject.toml", "does-not-exist"],
            )

            self.assertTrue((dest / "pyproject.toml").is_file())
            self.assertFalse((dest / "does-not-exist").exists())


class WheelBuildAllowlistTest(unittest.TestCase):
    """Behavior pins for :func:`wheel_build_allowlist`.

    The two production consumers in this repo build their allowlist via
    this helper because the canonical ``DEFAULT_ALLOWLIST`` was designed
    for a publish-clone-style layout (pyproject.toml as sibling of the
    package). In this repo, pyproject.toml and a large ``tests/`` tree
    both live *inside* ``weld/`` -- so we need a per-call enumeration
    that includes everything except the named test/dev exclusions.
    """

    def test_drops_tests_subtree(self) -> None:
        # Pins the most important exclusion: the ~6 MB tests/ tree must
        # never end up in a per-test temp copy.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "src"
            src.mkdir()
            _make_tree(src)

            allow = wheel_build_allowlist(src)

            self.assertIn("weld", allow)
            self.assertIn("pyproject.toml", allow)
            self.assertNotIn(
                "tests", allow,
                "tests/ must be excluded from the wheel-build allowlist",
            )

    def test_drops_pycache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "src"
            src.mkdir()
            _make_tree(src)
            (src / "__pycache__").mkdir()
            (src / "__pycache__" / "foo.pyc").write_bytes(b"cache")

            allow = wheel_build_allowlist(src)

            self.assertNotIn("__pycache__", allow)

    def test_result_is_sorted_for_determinism(self) -> None:
        # Sorted output lets the helper's output be diff-stable in any
        # debug log that prints the allowlist.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "src"
            src.mkdir()
            for name in ("zebra", "apple", "mango"):
                (src / name).mkdir()

            allow = wheel_build_allowlist(src)

            self.assertEqual(list(allow), sorted(allow))

    def test_used_as_copy_allowlist_excludes_tests(self) -> None:
        # End-to-end sanity: pass wheel_build_allowlist into
        # copy_weld_source and confirm tests/ never lands in dest.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "src"
            src.mkdir()
            _make_tree(src)
            dest = tmp_path / "dest"

            copy_weld_source(
                src, dest, allowlist=wheel_build_allowlist(src),
            )

            self.assertTrue((dest / "weld" / "__init__.py").is_file())
            self.assertTrue((dest / "pyproject.toml").is_file())
            self.assertFalse(
                (dest / "tests").exists(),
                "wheel_build_allowlist must filter out tests/",
            )


class CLIInterfaceTest(unittest.TestCase):
    """The module is also runnable as a script so that shell tests can
    stage a hermetic copy without depending on ``rsync`` availability in
    the Bazel build environment. This test pins that the CLI accepts the
    documented argument shape (``src dest [name ...]``) and produces the
    same result as the importable :func:`copy_weld_source`.
    """

    def test_cli_with_explicit_names(self) -> None:
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "src"
            src.mkdir()
            _make_tree(src)
            dest = tmp_path / "dest"

            proc = subprocess.run(
                [
                    sys.executable, "-m",
                    "weld.tests._source_tree_copy",
                    str(src), str(dest),
                    "pyproject.toml", "weld",
                ],
                cwd=str(_REPO_ROOT),
                capture_output=True, text=True, check=False,
            )

            self.assertEqual(
                proc.returncode, 0,
                f"CLI failed rc={proc.returncode} "
                f"stdout={proc.stdout!r} stderr={proc.stderr!r}",
            )
            self.assertTrue((dest / "pyproject.toml").is_file())
            self.assertTrue((dest / "weld" / "__init__.py").is_file())
            self.assertFalse((dest / "tests").exists())

    def test_cli_with_no_names_uses_default(self) -> None:
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "src"
            src.mkdir()
            _make_tree(src)
            dest = tmp_path / "dest"

            proc = subprocess.run(
                [
                    sys.executable, "-m",
                    "weld.tests._source_tree_copy",
                    str(src), str(dest),
                ],
                cwd=str(_REPO_ROOT),
                capture_output=True, text=True, check=False,
            )

            self.assertEqual(
                proc.returncode, 0,
                f"CLI failed rc={proc.returncode} "
                f"stdout={proc.stdout!r} stderr={proc.stderr!r}",
            )
            # Default list: ``weld`` + ``pyproject.toml`` + optional .weld.
            self.assertTrue((dest / "weld" / "__init__.py").is_file())
            self.assertTrue((dest / "pyproject.toml").is_file())
            self.assertFalse(
                (dest / "tests").exists(),
                "default allowlist leaked an unnamed child",
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
