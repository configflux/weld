"""Auto-recurse stale children on root reads (ADR 0066 part 3 / 00p8.3).

These tests pin the read-time refresh selector and the federated branch of
auto-refresh against **real git fixtures** (no stubbed ``git``), exactly as
users hit it: a workspace root whose child repo has moved past its graph.

Coverage map (ADR 0066 part 3 acceptance + the issue's three criteria):

* ``select_stale_children`` picks the stale-or-uninitialized subset and
  skips fresh / missing / corrupt children (proportional refresh).
* ``auto_refresh_federated_root`` respects ``WELD_AUTO_REFRESH=0`` and
  ``--no-refresh`` (no refresh; ``--no-refresh`` still warns naming the
  stale children) -- the CI / gate-freeze contract (bd 19tw).
* The empty-subset steady state returns ``None`` (no work, no lock churn).
* A stale child is refreshed in place, its sidecar regenerated, and the
  root meta-graph rewritten; a sibling fresh child is left byte-untouched.
* Per-child failure isolation: one child whose discovery raises does not
  break the refresh of the others and never propagates (RecurseResult.errors).
"""

from __future__ import annotations

import io
import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from weld._auto_refresh_federated import (
    auto_refresh_federated_root,
    select_stale_children,
)
from weld.workspace import ChildEntry, WorkspaceConfig, dump_workspaces_yaml
from weld.workspace_state import build_workspace_state, load_workspace_config


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


def _write_discover_yaml(repo_root: Path) -> None:
    weld_dir = repo_root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "discover.yaml").write_text("sources: []\n", encoding="utf-8")


def _write_child_graph(repo_root: Path, *, git_sha: str | None) -> None:
    """Write a child ``graph.json`` whose discovered-from SHA lives in the
    ADR 0065 sidecar (matches the real write path the oracle reads through).
    """
    weld_dir = repo_root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {"version": 1, "schema_version": 1, "discovered_from": ["."]},
        "nodes": {},
        "edges": [],
    }
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    if git_sha is not None:
        (weld_dir / "graph-meta.json").write_text(
            json.dumps({"version": 1, "git_sha": git_sha}, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def _commit_change(repo_root: Path, name: str = "feature.py") -> str:
    (repo_root / name).write_text("x = 1\n", encoding="utf-8")
    _git(repo_root, "add", name)
    _git(repo_root, "commit", "-q", "-m", f"add {name}")
    return _git(repo_root, "rev-parse", "HEAD")


def _write_workspaces(root: Path, children: list[ChildEntry]) -> WorkspaceConfig:
    config = WorkspaceConfig(children=children, cross_repo_strategies=[])
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    dump_workspaces_yaml(config, root / ".weld" / "workspaces.yaml")
    return config


def _commit_discover_yaml(child: Path) -> str:
    """Write + commit ``.weld/discover.yaml`` and return the resulting HEAD.

    Real children commit their generated ``discover.yaml`` (it is config, not
    weld bookkeeping). Leaving it untracked would make
    ``working_tree_dirty_sources`` report source drift for a ``discovered_from:
    ["."]`` child, so the fixture commits it -- the working tree must be clean
    for the oracle to call a child fresh (bd 85tb.1 working-tree-aware
    staleness). The child's ``graph.json`` / ``graph-meta.json`` are weld
    bookkeeping and are excluded from the working-tree check, so they may stay
    untracked.
    """
    _write_discover_yaml(child)
    _git(child, "add", ".weld/discover.yaml")
    _git(child, "commit", "-q", "-m", "weld init")
    return _git(child, "rev-parse", "HEAD")


def _fresh_child(root: Path, name: str) -> str:
    """A present child discovered at its own HEAD (oracle: fresh)."""
    child = _init_repo(root / name)
    head = _commit_discover_yaml(child)
    _write_child_graph(child, git_sha=head)
    return head


def _stale_child(root: Path, name: str) -> str:
    """A present child whose HEAD moved past its graph (oracle: source_changed)."""
    child = _init_repo(root / name)
    discovered = _commit_discover_yaml(child)
    _write_child_graph(child, git_sha=discovered)
    _commit_change(child)  # tracked-source commit past the graph SHA
    return discovered


class SelectStaleChildrenTest(unittest.TestCase):
    """The selector returns exactly the stale-or-uninitialized subset."""

    def test_selects_stale_and_uninitialized_skips_fresh_and_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fresh_child(root, "fresh-svc")
            _stale_child(root, "stale-svc")
            # uninitialized: git repo, discover.yaml, but no graph yet.
            uninit = _init_repo(root / "uninit-svc")
            _write_discover_yaml(uninit)
            config = _write_workspaces(root, [
                ChildEntry(name="fresh-svc", path="fresh-svc"),
                ChildEntry(name="stale-svc", path="stale-svc"),
                ChildEntry(name="uninit-svc", path="uninit-svc"),
                ChildEntry(name="ghost-svc", path="ghost-svc"),  # missing
            ])
            state = build_workspace_state(root, config)

            subset = select_stale_children(root, config, state)
            self.assertEqual(subset, {"stale-svc", "uninit-svc"})

    def test_empty_when_all_fresh(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fresh_child(root, "a")
            _fresh_child(root, "b")
            config = _write_workspaces(root, [
                ChildEntry(name="a", path="a"),
                ChildEntry(name="b", path="b"),
            ])
            state = build_workspace_state(root, config)
            self.assertEqual(select_stale_children(root, config, state), set())


class EnvAndNoRefreshOptOutTest(unittest.TestCase):
    """WELD_AUTO_REFRESH=0 and --no-refresh are honoured (bd 19tw freeze)."""

    def _root_with_stale_child(self, root: Path) -> None:
        _stale_child(root, "svc")
        _write_workspaces(root, [ChildEntry(name="svc", path="svc")])

    def test_env_disabled_returns_none_no_write(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._root_with_stale_child(root)
            before = (root / "svc" / ".weld" / "graph.json").read_text("utf-8")

            result = auto_refresh_federated_root(
                root, env={"WELD_AUTO_REFRESH": "0"}, stderr=io.StringIO(),
            )
            self.assertIsNone(result)
            after = (root / "svc" / ".weld" / "graph.json").read_text("utf-8")
            self.assertEqual(before, after, "env opt-out must not rewrite child")

    def test_no_refresh_warns_and_does_not_write(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._root_with_stale_child(root)
            before = (root / "svc" / ".weld" / "graph.json").read_text("utf-8")
            err = io.StringIO()

            result = auto_refresh_federated_root(
                root, no_refresh=True, env={}, stderr=err,
            )
            self.assertIsNone(result)
            after = (root / "svc" / ".weld" / "graph.json").read_text("utf-8")
            self.assertEqual(before, after)
            # Warning names the stale child so the operator knows what is stale.
            text = err.getvalue()
            self.assertIn("stale", text.lower())
            self.assertIn("svc", text)


class EmptySubsetNoopTest(unittest.TestCase):
    """All-fresh workspace: no refresh, no lock churn, returns None."""

    def test_all_fresh_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fresh_child(root, "svc")
            _write_workspaces(root, [ChildEntry(name="svc", path="svc")])
            result = auto_refresh_federated_root(root, env={}, stderr=io.StringIO())
            self.assertIsNone(result)

    def test_non_federated_root_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)  # plain repo, no workspaces.yaml
            self.assertIsNone(load_workspace_config(root))
            result = auto_refresh_federated_root(root, env={}, stderr=io.StringIO())
            self.assertIsNone(result)


class RefreshStaleSubsetTest(unittest.TestCase):
    """A stale child is refreshed in place; a fresh sibling is untouched."""

    def test_refreshes_only_stale_child_and_rebuilds_root(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fresh_child(root, "fresh-svc")
            stale_discovered = _stale_child(root, "stale-svc")
            _write_workspaces(root, [
                ChildEntry(name="fresh-svc", path="fresh-svc"),
                ChildEntry(name="stale-svc", path="stale-svc"),
            ])

            fresh_graph = root / "fresh-svc" / ".weld" / "graph.json"
            stale_graph = root / "stale-svc" / ".weld" / "graph.json"
            fresh_before = fresh_graph.read_text("utf-8")
            stale_before = stale_graph.read_text("utf-8")

            result = auto_refresh_federated_root(root, env={}, stderr=io.StringIO())

            self.assertIsNotNone(result)
            self.assertIn("stale-svc", result["refreshed_children"])
            self.assertNotIn("fresh-svc", result["refreshed_children"])
            # Fresh sibling untouched (proportional refresh).
            self.assertEqual(fresh_before, fresh_graph.read_text("utf-8"))
            # Stale child rewritten by in-process discovery.
            self.assertNotEqual(stale_before, stale_graph.read_text("utf-8"))
            # The child's discovered-from sidecar was regenerated to the new
            # HEAD, so the oracle now reports it fresh.
            new_meta = json.loads(
                (root / "stale-svc" / ".weld" / "graph-meta.json").read_text("utf-8"),
            )
            self.assertNotEqual(new_meta.get("git_sha"), stale_discovered)
            # Root meta-graph exists and carries both repo nodes.
            root_graph = json.loads(
                (root / ".weld" / "graph.json").read_text("utf-8"),
            )
            self.assertIn("repo:fresh-svc", root_graph["nodes"])
            self.assertIn("repo:stale-svc", root_graph["nodes"])

    def test_refresh_makes_subsequent_oracle_fresh(self) -> None:
        """After auto-refresh, the selector finds nothing stale (idempotent)."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _stale_child(root, "svc")
            config = _write_workspaces(root, [ChildEntry(name="svc", path="svc")])

            auto_refresh_federated_root(root, env={}, stderr=io.StringIO())

            state = build_workspace_state(root, config)
            self.assertEqual(select_stale_children(root, config, state), set())

    def test_refresh_regenerates_sidecar_no_rerefresh_loop(self) -> None:
        """A refreshed child with real sources is fresh on the next pass.

        Regression: ``recurse_children`` used to write only ``graph.json`` and
        leave the child's ``graph-meta.json`` holding the *old* discovered-from
        SHA. Because the sidecar wins over in-graph meta (ADR 0065), the oracle
        kept reporting the child stale after every refresh -- an auto-recurse
        re-refresh loop on every read. Uses a child with a **non-empty**
        ``discovered_from`` so the sidecar SHA is load-bearing (a ``sources:
        []`` child masks the bug via an empty tracked set).
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = _init_repo(root / "svc")
            # Real Python source + a discover.yaml that scans it -> non-empty
            # discovered_from after discovery.
            (child / "mod.py").write_text("x = 1\n", encoding="utf-8")
            (child / ".weld").mkdir(parents=True, exist_ok=True)
            (child / ".weld" / "discover.yaml").write_text(
                "sources:\n  - glob: '**/*.py'\n    type: file\n"
                "    strategy: python_module\n",
                encoding="utf-8",
            )
            # Real children gitignore the volatile .weld/ artifacts (wd init
            # writes this). Without it, the .weld/graph-previous.json the
            # refresh writes is untracked under discovered_from=['./'] and
            # working_tree_dirty_sources would falsely report source drift --
            # masking whether the *sidecar SHA* tracks HEAD.
            from weld._gitignore_writer import write_weld_gitignore
            write_weld_gitignore(child / ".weld")
            _git(child, "add", "-A")
            _git(child, "commit", "-q", "-m", "real source + discover.yaml")
            discovered = _git(child, "rev-parse", "HEAD")
            _write_child_graph(child, git_sha=discovered)
            # Move HEAD past the graph with a tracked-source change -> stale.
            _commit_change(child, "mod2.py")
            config = _write_workspaces(root, [ChildEntry(name="svc", path="svc")])

            state = build_workspace_state(root, config)
            self.assertIn("svc", select_stale_children(root, config, state))

            # First refresh.
            r1 = auto_refresh_federated_root(root, env={}, stderr=io.StringIO())
            self.assertIn("svc", r1["refreshed_children"])

            # Second pass: the sidecar must have been regenerated to the new
            # HEAD, so the child is now fresh and is NOT selected again.
            state2 = build_workspace_state(root, config)
            self.assertEqual(
                select_stale_children(root, config, state2), set(),
                "child still stale after refresh -> sidecar not regenerated "
                "(re-refresh loop)",
            )
            r2 = auto_refresh_federated_root(root, env={}, stderr=io.StringIO())
            self.assertIsNone(r2, "steady-state read must not re-refresh")


class PerChildFailureIsolationTest(unittest.TestCase):
    """One child failing to refresh does not break the others or the query."""

    def test_one_child_failure_isolated(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _stale_child(root, "good-svc")
            # Bad child: stale (so it is selected) but its discover.yaml is
            # malformed, so _discover_single_repo raises during recurse.
            bad = _init_repo(root / "bad-svc")
            discovered = _git(bad, "rev-parse", "HEAD")
            _write_child_graph(bad, git_sha=discovered)
            (bad / ".weld" / "discover.yaml").write_text(
                "sources: [: this is not valid yaml\n", encoding="utf-8",
            )
            _commit_change(bad)
            _write_workspaces(root, [
                ChildEntry(name="good-svc", path="good-svc"),
                ChildEntry(name="bad-svc", path="bad-svc"),
            ])

            good_graph = root / "good-svc" / ".weld" / "graph.json"
            good_before = good_graph.read_text("utf-8")

            # Must not raise even though bad-svc's discovery fails.
            result = auto_refresh_federated_root(root, env={}, stderr=io.StringIO())

            self.assertIsNotNone(result)
            # The good child was refreshed; the bad one is recorded as an error.
            self.assertIn("good-svc", result["refreshed_children"])
            self.assertIn("bad-svc", result["errors"])
            self.assertNotEqual(good_before, good_graph.read_text("utf-8"))


if __name__ == "__main__":
    unittest.main()
