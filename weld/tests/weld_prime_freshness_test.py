"""Freshness guidance tests for `wd prime` (ADR 0017 alignment).

bd-5038-p1a.4: prime used a local SHA-only drift check and nudged users
toward `wd discover` whenever `meta.git_sha != HEAD`. That destroyed
curated enrichment on graphs whose sources were unchanged. These tests
pin prime to the ADR 0017 source-file freshness model:

- SHA-only drift (no tracked source files changed) must NOT produce a
  `wd discover` action.
- source-stale cases must recommend the atomic
  `wd discover --output .weld/graph.json` form introduced by
  bd-5038-p1a.3.
- A graph with high description coverage and no tracked source changes
  must not receive a full-discovery recommendation.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld._git import get_git_sha  # noqa: E402
from weld.prime import prime  # noqa: E402


def _run(cmd: list[str], cwd: Path) -> str:
    result = subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True, timeout=20,
        env={**os.environ, "LC_ALL": "C"},
    )
    if result.returncode != 0:
        raise AssertionError(
            f"{' '.join(cmd)} failed (rc={result.returncode}):\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result.stdout.strip()


def _git_init(root: Path) -> None:
    _run(["git", "init", "--quiet"], root)
    _run(["git", "config", "user.email", "test@test.com"], root)
    _run(["git", "config", "user.name", "Test"], root)
    _run(["git", "config", "commit.gpgsign", "false"], root)


def _commit_all(root: Path, msg: str) -> None:
    _run(["git", "add", "-A"], root)
    _run(["git", "commit", "-m", msg, "--quiet"], root)


def _minimal_discover_yaml() -> str:
    return (
        "sources:\n"
        '  - glob: "src/**/*.py"\n'
        "    type: file\n"
        "    strategy: python_module\n"
    )


def _write_graph(
    root: Path,
    *,
    git_sha: str | None,
    discovered_from: list[str],
    nodes: dict,
) -> None:
    meta: dict = {
        "version": 1,
        "updated_at": "2026-04-22T00:00:00+00:00",
        "discovered_from": discovered_from,
    }
    if git_sha is not None:
        meta["git_sha"] = git_sha
    payload = {"meta": meta, "nodes": nodes, "edges": []}
    (root / ".weld" / "graph.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8",
    )


def _enriched_nodes(n: int = 10) -> dict:
    return {
        f"n{i}": {
            "id": f"n{i}", "type": "file", "label": f"n{i}",
            "props": {"description": f"semantic description for node {i}"},
        }
        for i in range(n)
    }


def _setup_weld_repo(root: Path) -> str:
    """Create a git repo with a tracked source file under src/ and a
    populated .weld/ directory. Returns the initial commit SHA."""
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (root / "README.md").write_text("hi\n", encoding="utf-8")
    (root / ".weld" / "discover.yaml").write_text(_minimal_discover_yaml())
    (root / ".weld" / "file-index.json").write_text("{}")
    # agent surface so the agent-surface check does not fire
    claude_dir = root / ".claude" / "commands"
    claude_dir.mkdir(parents=True)
    (claude_dir / "weld.md").write_text("test")
    _git_init(root)
    _commit_all(root, "initial")
    sha = get_git_sha(root)
    assert sha is not None
    return sha


class PrimeShaOnlyDriftTest(unittest.TestCase):
    """SHA-only drift (no tracked source change) must not recommend discover."""

    def test_sha_only_drift_does_not_recommend_full_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sha0 = _setup_weld_repo(root)
            _write_graph(
                root, git_sha=sha0, discovered_from=["src/"],
                nodes=_enriched_nodes(),
            )
            # Advance HEAD by changing an untracked path; no src/ change.
            (root / "README.md").write_text("hi again\n", encoding="utf-8")
            _commit_all(root, "readme tweak")

            output = prime(root)

            # No discover action, in either its old or new form, should
            # be recommended when only the git SHA drifted.
            self.assertNotIn("wd discover", output, output)
            # And no numbered Next-steps entry should include discover.
            if "Next steps:" in output:
                next_steps = output.split("Next steps:", 1)[1]
                self.assertNotIn("wd discover", next_steps, next_steps)

    def test_enriched_graph_unchanged_sources_reports_no_graph_action(self) -> None:
        """Regression: graph with high description coverage where no
        tracked source changed must not receive a full-discovery nudge."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sha0 = _setup_weld_repo(root)
            _write_graph(
                root, git_sha=sha0, discovered_from=["src/"],
                # 20 nodes all with descriptions -> 100% coverage
                nodes=_enriched_nodes(20),
            )
            # Advance HEAD without touching src/
            (root / "README.md").write_text("readme edit\n", encoding="utf-8")
            _commit_all(root, "docs only")

            output = prime(root)

            # Primary acceptance criterion for bd-5038-p1a.4:
            self.assertNotIn("wd discover", output, output)
            # The graph itself should be reported as current for this case.
            # We accept either "No actions needed" (when nothing else
            # fires) or at minimum an OK graph line plus no discover step.
            self.assertIn("graph.json", output)


class PrimeSourceStaleTest(unittest.TestCase):
    """Source-stale cases must recommend the atomic --output form."""

    def test_source_change_recommends_atomic_output_flag(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sha0 = _setup_weld_repo(root)
            _write_graph(
                root, git_sha=sha0, discovered_from=["src/"],
                nodes=_enriched_nodes(),
            )
            # Change a tracked source file and commit.
            (root / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")
            _commit_all(root, "source change")

            output = prime(root)

            self.assertIn("wd discover --output .weld/graph.json", output)

    def test_missing_graph_sha_recommends_atomic_output_flag(self) -> None:
        """When meta.git_sha is absent we cannot prove fidelity; prime
        must still recommend the atomic --output form, not the old
        stdout redirect."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_weld_repo(root)
            _write_graph(
                root, git_sha=None, discovered_from=["src/"],
                nodes=_enriched_nodes(),
            )

            output = prime(root)

            self.assertIn("wd discover --output .weld/graph.json", output)


class PrimeGraphOnlyCommitTest(unittest.TestCase):
    """End-to-end workflow test for bd-p1a.6.

    Simulates: `wd touch` stamps meta.git_sha to HEAD, user commits just
    .weld/graph.json, HEAD advances. Prime must then emit no graph
    action and no `wd touch` next step -- otherwise the advisory creates
    an infinite touch/commit/touch loop.
    """

    def _commit_graph_only(self, root: Path, msg: str) -> None:
        _run(["git", "add", ".weld/graph.json"], root)
        _run(["git", "commit", "-m", msg, "--quiet"], root)

    def test_touch_then_graph_commit_is_silent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sha0 = _setup_weld_repo(root)
            # Graph stamped to the initial SHA (as `wd touch` would do).
            _write_graph(
                root, git_sha=sha0, discovered_from=["src/"],
                nodes=_enriched_nodes(),
            )
            # Commit just the graph. HEAD advances.
            self._commit_graph_only(root, "commit enriched graph")

            output = prime(root)

            # No graph-related action or advisory should be emitted.
            self.assertNotIn("wd discover", output, output)
            self.assertNotIn("wd touch", output, output)
            self.assertNotIn("behind HEAD", output, output)
            # Graph should be reported as OK/up-to-date.
            self.assertIn("up to date", output)
            # Next steps, if present, must not mention graph/touch.
            if "Next steps:" in output:
                next_steps = output.split("Next steps:", 1)[1]
                self.assertNotIn("wd discover", next_steps, next_steps)
                self.assertNotIn("wd touch", next_steps, next_steps)

    def test_graph_commit_plus_source_change_still_recommends_discover(
        self,
    ) -> None:
        """Real source changes after a graph-only commit must still
        trigger the normal source-stale path."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sha0 = _setup_weld_repo(root)
            _write_graph(
                root, git_sha=sha0, discovered_from=["src/"],
                nodes=_enriched_nodes(),
            )
            # 1) graph-only commit -- benign.
            self._commit_graph_only(root, "commit enriched graph")
            # 2) tracked source change -- not benign.
            (root / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")
            _commit_all(root, "real source change")

            output = prime(root)
            self.assertIn("wd discover --output .weld/graph.json", output)


if __name__ == "__main__":
    unittest.main()
