"""Regression tests for federated meta-graph SHA stamping (bd-1776099136-5038-tqe2).

Background
----------
``wd discover`` in single-repo mode stamps ``meta.git_sha`` so that
:func:`weld._staleness.compute_stale_info` (and downstream ``wd prime``)
can report whether the on-disk graph still matches HEAD.

The federated path (``build_root_meta_graph``) historically skipped this
stamp -- the original docstring described it as "reserved for future use".
The result was that ``wd prime --agent all`` reported
"graph.json has no git SHA -- may be stale" immediately after a successful
federated discover, because :mod:`weld._staleness` interprets a missing
``meta.git_sha`` as ``source_stale = True``.

This module pins the fix end-to-end:

* :class:`BuildRootMetaGraphSHATest` -- direct unit coverage that
  ``build_root_meta_graph`` stamps ``meta.git_sha`` to ``HEAD`` whenever
  the workspace root sits inside a git repository, and gracefully omits
  the field when it does not.
* :class:`FederatedDiscoverFreshnessTest` -- the customer's repro:
  federated discover from a worktree, then assert
  ``graph['meta']['git_sha'] == HEAD`` and that
  :func:`weld._staleness.compute_stale_info` no longer reports
  ``source_stale``.
* :class:`FederatedPrimeStalenessSilentTest` -- ``wd prime``'s staleness
  block is empty after a fresh federated discover (no SHA-missing line).

The tests intentionally do not stub ``git``: each fixture initialises a
real repo (and where relevant, a real linked worktree) so the fix is
exercised against the same code path users hit in the field.
"""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from weld._git import get_git_sha
from weld._staleness import compute_stale_info
from weld.discover import discover
from weld.federation_root import build_root_meta_graph
from weld.workspace import ChildEntry, WorkspaceConfig, dump_workspaces_yaml
from weld.workspace_state import WorkspaceState, build_workspace_state


# ---------------------------------------------------------------------------
# Git fixture helpers (parallel to weld_federation_worktree_test.py)
# ---------------------------------------------------------------------------

def _git(repo_root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env={"LC_ALL": "C", "PATH": "/usr/bin:/usr/local/bin:/bin"},
        check=True,
    )
    return proc.stdout.strip()


def _init_repo(repo_root: Path) -> Path:
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-q")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Weld Test")
    (repo_root / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "commit", "-q", "-m", "initial commit")
    return repo_root


def _write_child_graph(repo_root: Path) -> None:
    weld_dir = repo_root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    payload = {"meta": {"version": 1, "schema_version": 1}, "nodes": {}, "edges": []}
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_workspaces(root: Path, children: list[ChildEntry]) -> WorkspaceConfig:
    config = WorkspaceConfig(children=children, cross_repo_strategies=[])
    dump_workspaces_yaml(config, root / ".weld" / "workspaces.yaml")
    return config


def _seed_two_child_workspace(main: Path) -> None:
    """Seed *main* with two child repos and a committed workspaces.yaml."""
    _init_repo(main / "services" / "api")
    _init_repo(main / "services" / "auth")
    _write_child_graph(main / "services" / "api")
    _write_child_graph(main / "services" / "auth")
    _write_workspaces(
        main,
        [
            ChildEntry(name="services-api", path="services/api"),
            ChildEntry(name="services-auth", path="services/auth"),
        ],
    )
    _git(main, "add", ".weld/workspaces.yaml")
    _git(main, "commit", "-q", "-m", "add workspaces.yaml")


# ---------------------------------------------------------------------------
# Layer 1 -- direct unit coverage of build_root_meta_graph
# ---------------------------------------------------------------------------

class BuildRootMetaGraphSHATest(unittest.TestCase):
    """``build_root_meta_graph`` must stamp meta.git_sha when *root* is a repo."""

    def test_stamps_meta_git_sha_to_head(self) -> None:
        with TemporaryDirectory() as tmp:
            main = _init_repo(Path(tmp) / "main")
            _seed_two_child_workspace(main)
            head = get_git_sha(main)
            self.assertIsNotNone(head)

            config = WorkspaceConfig(
                children=[
                    ChildEntry(name="services-api", path="services/api"),
                    ChildEntry(name="services-auth", path="services/auth"),
                ],
                cross_repo_strategies=[],
            )
            state = build_workspace_state(main, config, now="t0")

            graph = build_root_meta_graph(main, config, state, now="t0")
            self.assertIn("git_sha", graph["meta"])
            self.assertEqual(graph["meta"]["git_sha"], head)

    def test_omits_git_sha_when_root_is_not_a_repo(self) -> None:
        # Bare directory -- not a git repo. ``get_git_sha`` returns None and
        # ``build_root_meta_graph`` must not stamp the key at all (callers
        # rely on the absence of ``git_sha`` to detect non-git roots).
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            root.mkdir()
            (root / ".weld").mkdir()
            config = WorkspaceConfig(children=[], cross_repo_strategies=[])
            state = WorkspaceState(children={})

            graph = build_root_meta_graph(root, config, state, now="t0")
            self.assertNotIn("git_sha", graph["meta"])


# ---------------------------------------------------------------------------
# Layer 2 -- federated discover + worktree end-to-end
# ---------------------------------------------------------------------------

class FederatedDiscoverFreshnessTest(unittest.TestCase):
    """The customer's repro: discover from a worktree, then check freshness."""

    def test_federated_discover_stamps_head_inside_a_worktree(self) -> None:
        with TemporaryDirectory() as tmp:
            main = _init_repo(Path(tmp) / "main")
            _seed_two_child_workspace(main)

            wt = Path(tmp) / "wt"
            _git(main, "worktree", "add", "-q", str(wt), "-b", "feature")

            head = get_git_sha(wt)
            self.assertIsNotNone(head)

            graph = discover(wt, incremental=False)
            # Sanity: federation produced the repo nodes.
            self.assertEqual(
                sorted(graph["nodes"]),
                ["repo:services-api", "repo:services-auth"],
            )
            # The fix: meta.git_sha is stamped to HEAD of the worktree.
            self.assertEqual(graph["meta"].get("git_sha"), head)

    def test_compute_stale_info_does_not_report_source_stale_after_discover(
        self,
    ) -> None:
        with TemporaryDirectory() as tmp:
            main = _init_repo(Path(tmp) / "main")
            _seed_two_child_workspace(main)

            wt = Path(tmp) / "wt"
            _git(main, "worktree", "add", "-q", str(wt), "-b", "feature")

            target = wt / ".weld" / "graph.json"
            graph = discover(wt, incremental=False, output=target)

            info = compute_stale_info(target, graph["meta"])
            # Pre-fix: ``meta.git_sha`` was missing -> source_stale=True. Post-fix
            # the SHA is present and equal to HEAD, so source_stale must be False.
            self.assertFalse(info["source_stale"], info)
            self.assertEqual(info["graph_sha"], graph["meta"]["git_sha"])
            self.assertEqual(info["graph_sha"], info["current_sha"])


# ---------------------------------------------------------------------------
# Layer 3 -- ``wd prime`` staleness block stays silent after a fresh discover
# ---------------------------------------------------------------------------

class FederatedPrimeStalenessSilentTest(unittest.TestCase):
    """``_check_staleness`` must not emit the SHA-missing line post-fix."""

    def test_check_staleness_emits_no_sha_missing_line(self) -> None:
        from weld.prime import _check_staleness

        with TemporaryDirectory() as tmp:
            main = _init_repo(Path(tmp) / "main")
            _seed_two_child_workspace(main)

            wt = Path(tmp) / "wt"
            _git(main, "worktree", "add", "-q", str(wt), "-b", "feature")

            target = wt / ".weld" / "graph.json"
            graph = discover(wt, incremental=False, output=target)

            lines, steps = _check_staleness(graph, wt)
            joined = "\n".join(lines)
            self.assertNotIn("no git SHA", joined)
            # No staleness action should be queued for a freshly discovered graph.
            self.assertEqual(steps, [])


if __name__ == "__main__":
    unittest.main()
