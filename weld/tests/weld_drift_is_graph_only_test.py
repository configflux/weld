"""Direct unit coverage for ``drift_is_graph_only`` (ADR 0017 tracked issue).

Split out of ``weld_stale_source_test`` to hold that module under the
line-count cap; the two are otherwise unrelated in scope (that module covers
`Graph.stale()` end to end, this one is a focused unit suite for a single
helper), so the split costs nothing beyond the file boundary.

The customer-reported 5-step repro exercises this helper end-to-end via
`wd prime`; this file pins the helper against the cross product of the
bookkeeping paths so a future regression in the path set is caught locally.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from weld._git import drift_is_graph_only, get_git_sha  # noqa: E402


def _run(cmd: list[str], cwd: Path) -> None:
    result = subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True, timeout=20,
        env={**os.environ, "LC_ALL": "C"},
    )
    if result.returncode != 0:
        raise AssertionError(
            f"{' '.join(cmd)} failed (rc={result.returncode}):\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def _git_init(root: Path) -> None:
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    _run(["git", "init", "--quiet"], root)
    _run(["git", "config", "user.email", "test@test.com"], root)
    _run(["git", "config", "user.name", "Test"], root)
    _run(["git", "config", "commit.gpgsign", "false"], root)


class DriftIsGraphOnlyTest(unittest.TestCase):
    """Direct unit coverage for ``drift_is_graph_only``.

    The customer-reported 5-step repro in bd-...yb89 exercises this
    helper end-to-end via ``wd prime``; this file pins the helper
    against the cross product of the bookkeeping paths so a future
    regression in the path set is caught locally.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        _git_init(self.root)
        # Seed: one source file committed at sha0.
        (self.root / "app.py").write_text("x = 1\n", encoding="utf-8")
        _run(["git", "add", "app.py"], self.root)
        _run(["git", "commit", "--quiet", "-m", "seed"], self.root)
        self._sha0 = get_git_sha(self.root)

    def _commit(self, paths: list[str], message: str) -> None:
        for rel in paths:
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("{}\n", encoding="utf-8")
            _run(["git", "add", rel], self.root)
        _run(["git", "commit", "--quiet", "-m", message], self.root)

    def test_graph_only_commit_is_graph_only(self) -> None:
        self._commit([".weld/graph.json"], "graph")
        self.assertTrue(drift_is_graph_only(self.root, self._sha0))

    def test_state_only_commit_is_graph_only(self) -> None:
        self._commit([".weld/discovery-state.json"], "state")
        self.assertTrue(drift_is_graph_only(self.root, self._sha0))

    def test_both_bookkeeping_paths_is_graph_only(self) -> None:
        self._commit(
            [".weld/graph.json", ".weld/discovery-state.json"],
            "graph and state",
        )
        self.assertTrue(drift_is_graph_only(self.root, self._sha0))

    def test_source_change_is_not_graph_only(self) -> None:
        self._commit(["app.py"], "edit source")
        self.assertFalse(drift_is_graph_only(self.root, self._sha0))

    def test_mixed_change_is_not_graph_only(self) -> None:
        self._commit(
            [".weld/graph.json", "app.py"], "graph plus source",
        )
        self.assertFalse(drift_is_graph_only(self.root, self._sha0))

    def test_empty_diff_is_not_graph_only(self) -> None:
        # No commit between sha0 and HEAD.
        self.assertFalse(drift_is_graph_only(self.root, self._sha0))


if __name__ == "__main__":
    unittest.main()
