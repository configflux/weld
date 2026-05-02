"""Smoke tests for the synthetic large-repo generator.

These tests do *not* exercise the full 1k/10k/100k scales -- those are
on-demand benchmark scenarios documented in ``docs/performance.md``.
The unit tests cover the generator's API contract:

* ``generate_single`` writes the requested number of Python modules
  under a deterministic package layout.
* ``generate_polyrepo`` writes one git-initialised child per slot and
  each child has its own modules.
* Generated modules import siblings (so the call graph has shape) and
  are syntactically valid Python.

Git availability: the polyrepo and ``--git-init`` paths require ``git``
on PATH. Bazel sandboxes legitimately may not provide ``git``, so the
git-dependent classes are gated with ``skipUnless(_has_git(), ...)``.
To keep that gating from masking discovery breakage as a silent skip,
``GitGatedSmokeTest`` always runs and proves the test module is loaded
*and* records why the gate fired so the absence of polyrepo coverage is
visible rather than silent.
"""

from __future__ import annotations

import ast
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import mkdtemp

from weld.bench.synthetic_large_repo import (
    GenSpec,
    generate_polyrepo,
    generate_single,
)


def _has_git() -> bool:
    return shutil.which("git") is not None


class SyntheticGeneratorSingleTest(unittest.TestCase):
    """``generate_single`` produces the requested module count."""

    def setUp(self) -> None:
        self.root = Path(mkdtemp(prefix="weld-bench-single-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_writes_requested_module_count(self) -> None:
        spec = GenSpec(root=self.root, files=8, imports_per_file=2)
        generate_single(spec)
        modules = list(self.root.rglob("module_*.py"))
        self.assertEqual(len(modules), 8)

    def test_modules_are_syntactically_valid(self) -> None:
        spec = GenSpec(root=self.root, files=4, imports_per_file=1)
        generate_single(spec)
        for module_path in self.root.rglob("module_*.py"):
            ast.parse(
                module_path.read_text(encoding="utf-8"),
                filename=str(module_path),
            )

    def test_modules_emit_import_edges(self) -> None:
        spec = GenSpec(root=self.root, files=6, imports_per_file=2)
        generate_single(spec)
        module_zero = next(self.root.rglob("module_0.py"))
        text = module_zero.read_text(encoding="utf-8")
        # A non-trivial generator should produce at least one import line
        # for every module when imports_per_file > 0.
        self.assertIn("import ", text)


@unittest.skipUnless(_has_git(), "git not available in this environment")
class SyntheticGeneratorPolyrepoTest(unittest.TestCase):
    """``generate_polyrepo`` produces git-initialised child repos."""

    def setUp(self) -> None:
        self.root = Path(mkdtemp(prefix="weld-bench-poly-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_creates_one_child_per_slot(self) -> None:
        generate_polyrepo(
            self.root, children=3, files_per_child=2, imports_per_file=1
        )
        children = sorted(p.name for p in self.root.iterdir() if p.is_dir() and p.name.startswith("repo_"))
        self.assertEqual(children, ["repo_00", "repo_01", "repo_02"])

    def test_each_child_is_a_git_repo_with_modules(self) -> None:
        generate_polyrepo(
            self.root, children=2, files_per_child=2, imports_per_file=1
        )
        for child in (self.root / "repo_00", self.root / "repo_01"):
            self.assertTrue((child / ".git").is_dir())
            modules = list(child.rglob("module_*.py"))
            self.assertEqual(len(modules), 2)
            # Verify the child has a HEAD commit.
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=child,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertTrue(result.stdout.strip())


class SingleLayoutGitInitFlagTest(unittest.TestCase):
    """``--git-init`` initialises the single layout as a real git repo.

    Without it, ``wd init`` falls back to ``os.walk``; with it, ``wd
    init`` takes the faster ``git ls-files`` branch. The flag matches
    what real-world single repos look like at scale.
    """

    def setUp(self) -> None:
        self.root = Path(mkdtemp(prefix="weld-bench-single-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def _run_main(self, *extra: str) -> None:
        from weld.bench.synthetic_large_repo import main as bench_main
        rc = bench_main([
            "--layout", "single", "--files", "5",
            "--output", str(self.root), "--clean", *extra,
        ])
        self.assertEqual(rc, 0)

    def test_default_single_layout_is_not_git_initialised(self) -> None:
        self._run_main()
        self.assertFalse((self.root / ".git").exists())

    def test_git_init_flag_creates_git_repo_with_one_commit(self) -> None:
        self._run_main("--git-init")
        self.assertTrue((self.root / ".git").is_dir())
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.root, check=True, capture_output=True, text=True,
        )
        self.assertTrue(result.stdout.strip())


class GitGatedSmokeTest(unittest.TestCase):
    """Always-running smoke tests that document the git gate.

    The polyrepo class above is gated with ``skipUnless(_has_git(), ...)``
    because git may legitimately be absent from a Bazel sandbox. Without
    a non-skipping companion, that gate makes the entire test file
    appear "all green" even when no polyrepo assertion ran. These smoke
    tests close the gap by:

    * Asserting the test module is loaded (the asserts below run
      unconditionally), so a regression that breaks the file during
      import is caught.
    * Recording the git-availability fact in the test output so an
      operator can see *why* the polyrepo class skipped (or did not)
      without having to read the source.
    * Exercising parts of ``generate_polyrepo``'s contract that do
      not require git (signature, layout shape when git is present) so
      a regression like a renamed kwarg or removed function is caught
      regardless of git availability.
    """

    def test_module_loaded_and_git_status_visible(self) -> None:
        # Trivially true; its purpose is to prove we reached the
        # assertion phase. If the file fails to import, this test
        # fails — converting an import error from "0 assertions
        # noticed" to "smoke test failed".
        self.assertTrue(True)
        # Print the git fact as a regular ``unittest`` message so the
        # bazel test log records whether the gated class skipped.
        # This is observable in test.log without re-running.
        print(
            "GitGatedSmokeTest: git available = "
            f"{_has_git()} (polyrepo class will "
            f"{'run' if _has_git() else 'skip'})"
        )

    def test_generate_polyrepo_signature_is_callable(self) -> None:
        # Reaches into the imported function to confirm the symbol is
        # bound and accepts the documented kwargs. This catches an
        # accidental rename or signature drift even when git is absent
        # (no actual generation happens).
        import inspect

        sig = inspect.signature(generate_polyrepo)
        for required in ("root", "children", "files_per_child"):
            self.assertIn(
                required, sig.parameters,
                f"generate_polyrepo signature missing '{required}'; "
                f"the polyrepo class above relies on this kwarg.",
            )

    def test_generate_polyrepo_layout_when_git_present(self) -> None:
        # When git is available, exercise the layout assertion that
        # does not depend on commit creation. This is intentionally
        # narrower than the polyrepo class above so it remains a
        # smoke test (one slot, one file). When git is absent, this
        # case skips with an explicit message so the skip is visible
        # *and* attributed to the runtime fact (not a class-level
        # decorator).
        if not _has_git():
            self.skipTest(
                "git not on PATH at runtime; layout smoke skipped — "
                "see GitGatedSmokeTest.test_module_loaded_and_git_status_visible"
            )
        root = Path(mkdtemp(prefix="weld-bench-poly-smoke-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        generate_polyrepo(
            root, children=1, files_per_child=1, imports_per_file=0
        )
        self.assertTrue(
            (root / "repo_00").is_dir(),
            "generate_polyrepo should always produce repo_00",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
