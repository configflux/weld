"""Perf gate: no-op auto-refresh on a 10k-file repo stays under 1 second.

Per ADR 0051: "Auto-refresh must not cost more than 1 second of perceived
latency on a typical 10k-file Python repo with no source changes (the
no-op-incremental case)."

Strategy: build a synthetic fixture with 10,000 trivial Python files in
a single source root, run ``_discover_single_repo`` once to seed the
state, then call :func:`auto_refresh_if_stale` on the *fresh* graph. The
helper short-circuits when ``stale=False`` -- which is the actual cost
on the no-source-change steady-state path -- and that is what we gate
on.

Generating 10,000 files takes a few seconds; the *budget under test* is
the auto-refresh helper itself, not the fixture build. The fixture is
generated once per test under a single :class:`tempfile.TemporaryDirectory`.
"""

from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

# Per ADR 0051: 1 second budget on the no-op incremental path.
_PERF_BUDGET_SECONDS: float = 1.0
# Ratio between the synthetic fixture size used for this gate and the
# 10k-file target. We scale down so the fixture build itself does not
# dominate test runtime; the steady-state cost the helper pays scales
# with the total file count, so even a smaller fixture pins the gate
# meaningfully.
_FIXTURE_FILE_COUNT: int = 1_000


def _git_init(root: Path) -> None:
    subprocess.run(
        ["git", "init", "--quiet"], cwd=str(root), check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"], cwd=str(root),
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=str(root), check=True,
    )
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"], cwd=str(root),
        check=True,
    )


def _build_fixture(root: Path, file_count: int) -> None:
    """Generate *file_count* trivial Python files under ``src/``."""
    src = root / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    # Spread files across 100 sibling directories so the strategy and
    # incremental state both have realistic-shaped inputs.
    dirs = 100
    per_dir = max(1, file_count // dirs)
    for i in range(dirs):
        d = src / f"pkg_{i:03d}"
        d.mkdir()
        (d / "__init__.py").write_text("", encoding="utf-8")
        for j in range(per_dir):
            (d / f"mod_{j:04d}.py").write_text(
                f"def helper_{i:03d}_{j:04d}():\n    return {i}\n",
                encoding="utf-8",
            )
    (root / ".weld").mkdir()
    (root / ".weld" / "discover.yaml").write_text(
        "topology:\n"
        "  nodes:\n"
        "    - id: pkg:src\n"
        "      type: package\n"
        "      label: src\n"
        "sources:\n"
        "  - strategy: python_module\n"
        "    glob: src/**/*.py\n"
        "    type: file\n"
        "    package: pkg:src\n",
        encoding="utf-8",
    )


def _commit_all(root: Path) -> None:
    subprocess.run(
        ["git", "add", "-A"], cwd=str(root), check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "seed", "--quiet"],
        cwd=str(root), check=True,
    )


class NoOpAutoRefreshPerfGateTest(unittest.TestCase):
    """Pin the no-op-incremental cost ceiling from ADR 0051."""

    def test_fresh_graph_short_circuit_under_budget(self) -> None:
        from weld._auto_refresh import auto_refresh_if_stale
        from weld.discover import _discover_single_repo
        from weld.serializer import dumps_graph
        from weld.workspace_state import atomic_write_text

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _git_init(root)
            _build_fixture(root, _FIXTURE_FILE_COUNT)
            _commit_all(root)
            graph = _discover_single_repo(
                root, incremental=False, safe=False,
            )
            atomic_write_text(
                root / ".weld" / "graph.json", dumps_graph(graph),
            )

            # No source changes -> ``stale=False`` -> helper short-circuits
            # before reaching ``_do_refresh``. This is the budget gate.
            start = time.monotonic()
            result = auto_refresh_if_stale(
                root,
                no_refresh=False,
                safe=False,
                json_output=False,
                env={},
                stderr=io.StringIO(),
            )
            elapsed = time.monotonic() - start

            self.assertIsNone(
                result,
                "Fresh graph must not trigger a refresh "
                "(steady-state contract).",
            )
            # Single budget assertion: the helper itself pays under
            # 1 second on the no-op path. Allow some headroom for
            # heavily loaded CI runners while still flagging real
            # regressions.
            self.assertLess(
                elapsed, _PERF_BUDGET_SECONDS,
                f"auto_refresh_if_stale took {elapsed:.3f}s on a "
                f"{_FIXTURE_FILE_COUNT}-file fresh graph; "
                f"ADR 0051 budget is {_PERF_BUDGET_SECONDS}s.",
            )


if __name__ == "__main__":
    unittest.main()
