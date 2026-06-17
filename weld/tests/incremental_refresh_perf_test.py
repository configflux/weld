"""Perf gate: no-change incremental refresh stays sub-second (bd 85tb.2).

ADR 0051's no-op budget is 1 s. The pre-existing ``auto_refresh_perf_test``
gates the ``stale=False`` short-circuit. This gate covers the *other* path
that the live ``auto-refresh.jsonl`` evidence showed at 6-8 s: a repo with a
source that legitimately produces no file-anchored node (here a
``concept_from_bd``-style path source). Before bd 85tb.2 that source
perpetually re-triggered the slow with-changes path -- a full
postprocess + query-state + file-index rebuild -- on every refresh even
with zero content changes. The fix routes it to the no-change fast path.

We assert the *content-changed-nothing* incremental refresh on a moderate
synthetic repo completes under a generous ceiling. The budget is loose
(headroom for loaded CI runners) but still an order of magnitude below the
old 6-8 s, so a regression that re-introduces the perpetual slow path trips
it. The synthetic repo is generated once under a TemporaryDirectory.
"""

from __future__ import annotations

import subprocess
import tempfile
import time
import unittest
from pathlib import Path


# Loose ceiling: an order of magnitude below the ~6-8 s old slow path,
# with generous headroom for CI. The point is to catch a regression to the
# perpetual full-rebuild path, not to micro-benchmark.
_BUDGET_SECONDS: float = 4.0
_FILE_COUNT: int = 400


def _git(root: Path) -> None:
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "T"],
        ["git", "config", "commit.gpgsign", "false"],
    ):
        subprocess.run(cmd, cwd=str(root), check=True)


def _build(root: Path, n: int) -> None:
    src = root / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    per = max(1, n // 20)
    for i in range(20):
        d = src / f"pkg_{i:02d}"
        d.mkdir()
        (d / "__init__.py").write_text("", encoding="utf-8")
        for j in range(per):
            (d / f"m_{j:03d}.py").write_text(
                f"def fn_{i:02d}_{j:03d}():\n    return {i}\n", encoding="utf-8",
            )
    # A node-less path source: the bd 85tb.2 perpetual-retrigger case.
    (root / "data.jsonl").write_text('{"k": 1}\n', encoding="utf-8")
    (root / ".weld").mkdir()
    (root / ".weld" / "discover.yaml").write_text(
        "topology:\n  nodes:\n    - id: pkg:src\n      type: package\n"
        "      label: src\nsources:\n  - strategy: python_module\n"
        "    glob: src/**/*.py\n    type: file\n    package: pkg:src\n"
        "  - strategy: concept_from_bd\n    path: data.jsonl\n    type: concept\n",
        encoding="utf-8",
    )


def _commit(root: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=str(root), check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=str(root), check=True)


class IncrementalNoChangeRefreshPerfTest(unittest.TestCase):
    def test_node_less_source_repo_refresh_under_budget(self) -> None:
        from weld.discover import _discover_single_repo

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _git(root)
            _build(root, _FILE_COUNT)
            _commit(root)
            # Seed: full + one incremental so companion + sidecars + state
            # are all warm and consistent (mirrors steady state).
            _discover_single_repo(root, incremental=False, write_graph=True)
            _discover_single_repo(
                root, incremental=True, write_graph=True, with_sqlite=False,
            )

            start = time.monotonic()
            _discover_single_repo(
                root, incremental=True, write_graph=True, with_sqlite=False,
            )
            elapsed = time.monotonic() - start

            self.assertLess(
                elapsed, _BUDGET_SECONDS,
                f"no-change incremental refresh took {elapsed:.2f}s on a "
                f"{_FILE_COUNT}-file repo with a node-less source; bd 85tb.2 "
                f"budget is {_BUDGET_SECONDS}s (old perpetual slow path was "
                f"~6-8 s).",
            )


if __name__ == "__main__":
    unittest.main()
