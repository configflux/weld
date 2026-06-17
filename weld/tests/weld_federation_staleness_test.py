"""Unit coverage for the federated child-staleness oracle (ADR 0066 part 1).

``child_stale_info(root, child)`` is the single source of truth for both
surfacing (``wd workspace status`` / ``wd stale``) and the future
auto-recurse refresh selector (00p8.3). These tests pin its rule order
against real git fixtures -- no stubbed ``git`` -- so the oracle is
exercised exactly as users hit it:

* lifecycle short-circuit (``missing`` / ``uninitialized`` / ``corrupt``
  are never ``stale``);
* source-staleness primary, reading the discovered-from SHA through the
  ADR 0065 sidecar seam (``load_graph_meta``), not child graph ``meta``;
* ledger-digest drift secondary (``reason="graph_drift"``);
* failure isolation (a probe that raises yields ``state="unknown"``,
  never propagates).
"""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from weld._federation_staleness import child_stale_info
from weld.workspace import ChildEntry, WorkspaceConfig
from weld.workspace_state import build_workspace_state


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


def _write_child_graph(repo_root: Path, *, git_sha: str | None) -> None:
    """Write a child ``graph.json`` whose discovered-from SHA lives in the
    ADR 0065 sidecar (``graph-meta.json``), matching the real write path.
    """
    weld_dir = repo_root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        # ``discovered_from: ["."]`` mirrors the default ``wd init`` shape:
        # any tracked-source change between the graph SHA and HEAD counts as
        # source drift (weld bookkeeping is excluded by ``_git``).
        "meta": {"version": 1, "schema_version": 1, "discovered_from": ["."]},
        "nodes": {},
        "edges": [],
    }
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if git_sha is not None:
        (weld_dir / "graph-meta.json").write_text(
            json.dumps({"version": 1, "git_sha": git_sha}, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def _config(*children: ChildEntry) -> WorkspaceConfig:
    return WorkspaceConfig(children=list(children), cross_repo_strategies=[])


def _commit_change(repo_root: Path, name: str = "feature.py") -> str:
    (repo_root / name).write_text("x = 1\n", encoding="utf-8")
    _git(repo_root, "add", name)
    _git(repo_root, "commit", "-q", "-m", f"add {name}")
    return _git(repo_root, "rev-parse", "HEAD")


class FreshChildTest(unittest.TestCase):
    """A present child discovered at HEAD is fresh."""

    def test_fresh_when_sidecar_sha_matches_head(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = _init_repo(root / "svc")
            head = _git(child, "rev-parse", "HEAD")
            _write_child_graph(child, git_sha=head)
            cfg = _config(ChildEntry(name="svc", path="svc"))
            state = build_workspace_state(root, cfg, now="t0")

            info = child_stale_info(root, state.children["svc"])
            self.assertFalse(info["stale"], info)
            self.assertEqual(info["state"], "fresh")
            self.assertEqual(info["reason"], "fresh")
            self.assertEqual(info["head_sha"], head)
            self.assertEqual(info["graph_sha"], head)


class SourceChangedTest(unittest.TestCase):
    """A present child whose HEAD moved past its graph is stale."""

    def test_stale_when_commits_past_graph(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = _init_repo(root / "svc")
            discovered = _git(child, "rev-parse", "HEAD")
            _write_child_graph(child, git_sha=discovered)
            new_head = _commit_change(child)
            self.assertNotEqual(discovered, new_head)
            cfg = _config(ChildEntry(name="svc", path="svc"))
            state = build_workspace_state(root, cfg, now="t0")

            info = child_stale_info(root, state.children["svc"])
            self.assertTrue(info["stale"], info)
            self.assertEqual(info["state"], "stale")
            self.assertEqual(info["reason"], "source_changed")
            self.assertEqual(info["head_sha"], new_head)
            self.assertEqual(info["graph_sha"], discovered)
            self.assertEqual(info["commits_behind"], 1)


class UnknownShaTest(unittest.TestCase):
    """A present child whose sidecar was never fetched reports unknown_sha.

    ADR 0066 part 1 rule 2: an absent discovered-from SHA is conservatively
    treated as stale (the child is refreshed, its sidecar regenerated).
    Reading child graph ``meta`` directly would silently see no SHA -- this
    test pins that the oracle goes through ``load_graph_meta`` and treats a
    missing sidecar as ``unknown_sha`` rather than fresh.
    """

    def test_stale_unknown_sha_when_sidecar_absent(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = _init_repo(root / "svc")
            _write_child_graph(child, git_sha=None)  # no sidecar
            cfg = _config(ChildEntry(name="svc", path="svc"))
            state = build_workspace_state(root, cfg, now="t0")

            info = child_stale_info(root, state.children["svc"])
            self.assertTrue(info["stale"], info)
            self.assertEqual(info["state"], "stale")
            self.assertEqual(info["reason"], "unknown_sha")
            self.assertIsNone(info["graph_sha"])
            # compute_stale_info's -1 "no SHA" sentinel is normalised to a
            # non-negative count for the stable oracle dict (ADR 0066 shape).
            self.assertEqual(info["commits_behind"], 0)


class GraphDriftTest(unittest.TestCase):
    """Source-fresh child whose graph bytes changed since the ledger recorded
    them reports ``graph_drift`` (ADR 0066 part 1 rule 3 / ADR 0011 §5)."""

    def test_stale_graph_drift_when_digest_changed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = _init_repo(root / "svc")
            head = _git(child, "rev-parse", "HEAD")
            _write_child_graph(child, git_sha=head)
            cfg = _config(ChildEntry(name="svc", path="svc"))
            state = build_workspace_state(root, cfg, now="t0")

            # Out-of-band re-discover: child graph.json bytes change while
            # HEAD (and the sidecar SHA) stay put -> source is fresh but the
            # ledger digest is stale.
            graph_path = child / ".weld" / "graph.json"
            payload = json.loads(graph_path.read_text(encoding="utf-8"))
            payload["nodes"] = {"entity:Added": {"type": "entity", "label": "Added", "props": {}}}
            graph_path.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            info = child_stale_info(root, state.children["svc"])
            self.assertTrue(info["stale"], info)
            self.assertEqual(info["reason"], "graph_drift")


class LifecycleShortCircuitTest(unittest.TestCase):
    """missing / uninitialized / corrupt are never ``stale``."""

    def test_missing_child_not_stale(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            # No child directory at all -> ledger status 'missing'.
            cfg = _config(ChildEntry(name="ghost", path="ghost"))
            state = build_workspace_state(root, cfg, now="t0")
            self.assertEqual(state.children["ghost"].status, "missing")

            info = child_stale_info(root, state.children["ghost"])
            self.assertFalse(info["stale"], info)
            self.assertEqual(info["state"], "missing")
            self.assertEqual(info["reason"], "missing")

    def test_uninitialized_child_not_stale(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root / "svc")  # repo but no .weld/graph.json
            cfg = _config(ChildEntry(name="svc", path="svc"))
            state = build_workspace_state(root, cfg, now="t0")
            self.assertEqual(state.children["svc"].status, "uninitialized")

            info = child_stale_info(root, state.children["svc"])
            self.assertFalse(info["stale"], info)
            self.assertEqual(info["state"], "uninitialized")

    def test_corrupt_child_not_stale(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = _init_repo(root / "svc")
            weld_dir = child / ".weld"
            weld_dir.mkdir()
            (weld_dir / "graph.json").write_text("{ not json", encoding="utf-8")
            cfg = _config(ChildEntry(name="svc", path="svc"))
            state = build_workspace_state(root, cfg, now="t0")
            self.assertEqual(state.children["svc"].status, "corrupt")

            info = child_stale_info(root, state.children["svc"])
            self.assertFalse(info["stale"], info)
            self.assertEqual(info["state"], "corrupt")


class NonGitChildTest(unittest.TestCase):
    """A present child that is not a git repo follows the ADR 0017 non-git
    path: never stale. (Edge case: graph.json present in a non-git dir.)"""

    def test_non_git_child_not_stale(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            # build_workspace_state requires a .git to call a child 'present',
            # so construct the ledger entry directly for this edge case.
            child = root / "svc"
            (child / ".weld").mkdir(parents=True)
            _write_child_graph(child, git_sha=None)
            from weld.workspace_state import WorkspaceChildState

            entry = WorkspaceChildState(
                status="present",
                head_sha=None,
                head_ref=None,
                is_dirty=False,
                graph_path="svc/.weld/graph.json",
                graph_sha256=None,
                last_seen_utc="t0",
            )
            info = child_stale_info(root, entry)
            self.assertFalse(info["stale"], info)
            self.assertEqual(info["reason"], "not a git repo")


class FailureIsolationTest(unittest.TestCase):
    """Any exception while probing one child yields state='unknown', never
    raises into the caller (ADR 0066 part 1 closing rule)."""

    def test_probe_exception_isolated(self) -> None:
        from weld.workspace_state import WorkspaceChildState
        from weld import _federation_staleness as fs

        entry = WorkspaceChildState(
            status="present",
            head_sha="abc",
            head_ref="refs/heads/main",
            is_dirty=False,
            graph_path="svc/.weld/graph.json",
            graph_sha256="deadbeef",
            last_seen_utc="t0",
        )

        original = fs.compute_stale_info

        def _boom(*_a, **_k):  # noqa: ANN002, ANN003
            raise RuntimeError("disk on fire")

        fs.compute_stale_info = _boom  # type: ignore[assignment]
        try:
            with TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "svc" / ".weld").mkdir(parents=True)
                (root / "svc" / ".git").mkdir()
                info = child_stale_info(root, entry)
        finally:
            fs.compute_stale_info = original  # type: ignore[assignment]

        self.assertFalse(info["stale"], info)
        self.assertEqual(info["state"], "unknown")
        self.assertIn("disk on fire", info["reason"])


if __name__ == "__main__":
    unittest.main()
