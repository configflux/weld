"""Shared fixture for the dirty-worktree staleness suites (bd 0jay).

One committed repo, one real graph, one real discovery inventory --
extracted so ``weld_dirty_worktree_settles_test`` and its full-enumeration
sibling never drift apart on what the fixture tree looks like. Mirrors the
``weld/tests/_coverage_stale_lib.py`` split for the ADR 0101 coverage
suites.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from weld._graph_meta_sidecar import load_graph_meta
from weld._staleness import compute_stale_info
from weld.discover import _discover_single_repo

CONFIG = """sources:
  - glob: "src/*.py"
    type: file
    strategy: python_module
  - glob: "*.md"
    type: doc
    strategy: markdown
"""

TREE: dict[str, str] = {
    "src/a.py": "def a():\n    return 1\n",
    "src/b.py": "def b():\n    return 2\n",
    "README.md": "# hi\n",
    # Matched by no source entry: dirt here is not a graph input.
    "notes.txt": "text\n",
}


def run(cmd: list[str], cwd: Path) -> None:
    result = subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True, timeout=30,
        env={**os.environ, "LC_ALL": "C"},
    )
    if result.returncode != 0:
        raise AssertionError(
            f"{' '.join(cmd)} failed (rc={result.returncode}):\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


class DirtyTreeFixture(unittest.TestCase):
    """A committed repo with a real graph and a real discovery inventory."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / ".weld").mkdir(parents=True, exist_ok=True)
        run(["git", "init", "--quiet"], self.root)
        run(["git", "config", "user.email", "test@test.com"], self.root)
        run(["git", "config", "user.name", "Test"], self.root)
        run(["git", "config", "commit.gpgsign", "false"], self.root)
        (self.root / ".weld" / "discover.yaml").write_text(
            CONFIG, encoding="utf-8"
        )
        for rel, body in TREE.items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        run(["git", "add", "-A"], self.root)
        run(["git", "commit", "-m", "initial", "--quiet"], self.root)
        self.discover()

    def discover(self) -> None:
        """Run real discovery, writing graph.json and the inventory."""
        _discover_single_repo(self.root, incremental=None, write_graph=True)

    @property
    def graph_path(self) -> Path:
        return self.root / ".weld" / "graph.json"

    def dirty(self, discovered_from: list[str]) -> list[str]:
        """The dirty set the staleness check sees, for anti-vacuity asserts."""
        from weld._git import working_tree_dirty_sources

        return working_tree_dirty_sources(
            self.root, discovered_from, detect_renames=False
        )

    def stale(self, *, discovered_from: list[str] | None = None) -> dict:
        """``compute_stale_info`` over the graph discovery just wrote."""
        meta = dict(load_graph_meta(self.graph_path))
        if discovered_from is not None:
            meta["discovered_from"] = discovered_from
        return compute_stale_info(self.graph_path, meta)

    def assertSettled(self, msg: str) -> None:
        info = self.stale()
        self.assertFalse(info["source_stale"], f"{msg}: {info}")
        self.assertFalse(info["stale"], f"{msg}: {info}")

    def assertStale(self, msg: str) -> None:
        info = self.stale()
        self.assertTrue(info["source_stale"], f"{msg}: {info}")
