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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
