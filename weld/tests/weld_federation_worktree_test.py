"""Acceptance tests for ADR 0028 -- federation worktree resolution and empty-graph guard.

Two layers, both required:

* :func:`weld._git.git_main_checkout_path` and the
  :func:`weld._workspace_inspect.resolve_child_root` helper that uses it
  resolve a child repo via the main worktree's checkout when the
  current worktree does not contain it.
* :func:`weld._discover_empty_guard.enforce_nonempty_federated_write`
  refuses to clobber a >0-node federated graph with a 0-node new graph
  unless ``--allow-empty`` is set.

The tests intentionally do not stub ``git``: each fixture initialises
real repos and (where necessary) real linked worktrees so the helper
is exercised end-to-end. Sandboxed CI without a writable temp dir
would fail anyway because the rest of the discover suite already
requires this.
"""

from __future__ import annotations

import io
import json
import subprocess
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory

from weld._discover_empty_guard import (
    EmptyFederatedGraphRefusedError,
    enforce_nonempty_federated_write,
    existing_node_count,
    missing_child_names,
)
from weld._git import git_main_checkout_path
from weld._workspace_inspect import inspect_child, resolve_child_root
from weld.contract import SCHEMA_VERSION
from weld.discover import discover
from weld.workspace import ChildEntry, WorkspaceConfig, dump_workspaces_yaml
from weld.workspace_state import WORKSPACE_STATE_FILENAME, WorkspaceChildState, WorkspaceState


# ---------------------------------------------------------------------------
# Git fixture helpers (same convention as weld_root_discovery_test.py)
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
    payload = {
        "meta": {"version": SCHEMA_VERSION, "schema_version": 1},
        "nodes": {},
        "edges": [],
    }
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_workspaces(root: Path, children: list[ChildEntry]) -> WorkspaceConfig:
    config = WorkspaceConfig(children=children, cross_repo_strategies=[])
    dump_workspaces_yaml(config, root / ".weld" / "workspaces.yaml")
    return config


# Layer 1 -- unit tests for git_main_checkout_path()

class GitMainCheckoutPathTest(unittest.TestCase):
    """Direct coverage for :func:`weld._git.git_main_checkout_path`."""

    def test_returns_none_outside_a_git_repo(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertIsNone(git_main_checkout_path(Path(tmp)))

    def test_returns_none_when_root_is_the_main_worktree(self) -> None:
        with TemporaryDirectory() as tmp:
            main = _init_repo(Path(tmp) / "main")
            # Inside the main worktree --git-common-dir resolves to ``.git``
            # under the same checkout, so the helper must return None.
            self.assertIsNone(git_main_checkout_path(main))

    def test_returns_main_checkout_inside_a_linked_worktree(self) -> None:
        with TemporaryDirectory() as tmp:
            main = _init_repo(Path(tmp) / "main")
            wt = Path(tmp) / "wt"
            _git(main, "worktree", "add", "-q", str(wt), "-b", "feature")
            resolved = git_main_checkout_path(wt)
            self.assertIsNotNone(resolved)
            self.assertEqual(Path(resolved).resolve(), main.resolve())


# Layer 2 -- inspect_child / resolve_child_root worktree fallback

class InspectChildWorktreeFallbackTest(unittest.TestCase):
    """Children that exist only at the main checkout must report ``present``."""

    def test_resolve_child_root_falls_back_to_main_worktree(self) -> None:
        with TemporaryDirectory() as tmp:
            main = _init_repo(Path(tmp) / "main")
            child = _init_repo(main / "services" / "api")
            _write_child_graph(child)
            wt = Path(tmp) / "wt"
            _git(main, "worktree", "add", "-q", str(wt), "-b", "feature")

            resolved = resolve_child_root(wt, "services/api")
            # The fallback resolves under the main checkout, not the worktree.
            self.assertTrue((resolved / ".git").exists())
            self.assertEqual(resolved.resolve(), child.resolve())

    def test_inspect_child_marks_present_via_main_worktree(self) -> None:
        with TemporaryDirectory() as tmp:
            main = _init_repo(Path(tmp) / "main")
            child = _init_repo(main / "services" / "api")
            _write_child_graph(child)
            wt = Path(tmp) / "wt"
            _git(main, "worktree", "add", "-q", str(wt), "-b", "feature")

            kwargs = inspect_child(wt, "services/api", remote=None, seen_at="t0")
            self.assertEqual(kwargs["status"], "present")
            self.assertIsNotNone(kwargs["head_sha"])
            # graph_path is recorded as a workspace-relative POSIX path.
            self.assertEqual(kwargs["graph_path"], "services/api/.weld/graph.json")

    def test_inspect_child_still_missing_when_not_at_either_path(self) -> None:
        with TemporaryDirectory() as tmp:
            main = _init_repo(Path(tmp) / "main")
            wt = Path(tmp) / "wt"
            _git(main, "worktree", "add", "-q", str(wt), "-b", "feature")
            kwargs = inspect_child(wt, "services/nope", remote=None, seen_at="t0")
            self.assertEqual(kwargs["status"], "missing")


# Layer 3 -- federated discover end-to-end inside a worktree

class FederatedDiscoverInsideWorktreeTest(unittest.TestCase):
    """The customer's repro: federated discover from inside a worktree."""

    def test_children_resolve_present_and_meta_graph_has_repo_nodes(self) -> None:
        with TemporaryDirectory() as tmp:
            main = _init_repo(Path(tmp) / "main")
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
            # Commit the federation registry so the linked worktree
            # (added below) sees it. The customer's repro has
            # ``workspaces.yaml`` checked in at the root, then a
            # worktree of that root is created -- the worktree has the
            # registry but lacks the (nested, untracked) child repos.
            _git(main, "add", ".weld/workspaces.yaml")
            _git(main, "commit", "-q", "-m", "add workspaces.yaml")

            # Seed an initial committed graph at the main checkout so the
            # guard would fire if the worktree fallback ever regressed.
            initial = discover(main, incremental=False)
            self.assertEqual(
                sorted(initial["nodes"]),
                ["repo:services-api", "repo:services-auth"],
            )

            wt = Path(tmp) / "wt"
            _git(main, "worktree", "add", "-q", str(wt), "-b", "feature")

            from_worktree = discover(wt, incremental=False)
            self.assertEqual(
                sorted(from_worktree["nodes"]),
                ["repo:services-api", "repo:services-auth"],
            )
            ledger = json.loads(
                (wt / ".weld" / WORKSPACE_STATE_FILENAME).read_text(encoding="utf-8"),
            )
            self.assertEqual(ledger["children"]["services-api"]["status"], "present")
            self.assertEqual(ledger["children"]["services-auth"]["status"], "present")


# Layer 4 -- empty-graph guard on federated atomic write

def _build_state_with_missing(names: list[str]) -> WorkspaceState:
    children = {
        name: WorkspaceChildState(
            status="missing",
            head_sha=None,
            head_ref=None,
            is_dirty=False,
            graph_path=f"{name}/.weld/graph.json",
            graph_sha256=None,
            last_seen_utc="t0",
        )
        for name in names
    }
    return WorkspaceState(children=children)


class ExistingNodeCountTest(unittest.TestCase):
    def test_counts_dict_nodes(self) -> None:
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "graph.json"
            target.write_text(json.dumps({"nodes": {"a": {}, "b": {}}}), encoding="utf-8")
            self.assertEqual(existing_node_count(target), 2)

    def test_counts_list_nodes(self) -> None:
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "graph.json"
            target.write_text(json.dumps({"nodes": [{}, {}, {}]}), encoding="utf-8")
            self.assertEqual(existing_node_count(target), 3)

    def test_returns_zero_for_missing_or_corrupt_files(self) -> None:
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "graph.json"
            self.assertEqual(existing_node_count(target), 0)
            target.write_text("not json", encoding="utf-8")
            buf = io.StringIO()
            with redirect_stderr(buf):
                self.assertEqual(existing_node_count(target), 0)
            self.assertRegex(buf.getvalue(), r"\[weld\] warning.+inactive")


class EnforceNonemptyFederatedWriteTest(unittest.TestCase):
    def test_passes_when_new_graph_has_nodes(self) -> None:
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "graph.json"
            target.write_text(json.dumps({"nodes": {"old": {}}}), encoding="utf-8")
            enforce_nonempty_federated_write(
                target,
                {"nodes": {"new": {}}},
                _build_state_with_missing([]),
                allow_empty=False,
            )

    def test_passes_when_prior_was_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "graph.json"
            target.write_text(json.dumps({"nodes": {}}), encoding="utf-8")
            enforce_nonempty_federated_write(
                target,
                {"nodes": {}},
                _build_state_with_missing([]),
                allow_empty=False,
            )

    def test_passes_when_target_does_not_exist_yet(self) -> None:
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "graph.json"
            enforce_nonempty_federated_write(
                target,
                {"nodes": {}},
                _build_state_with_missing([]),
                allow_empty=False,
            )

    def test_refuses_when_prior_nonempty_and_new_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "graph.json"
            prior_text = json.dumps({"nodes": {"keep": {}}})
            target.write_text(prior_text, encoding="utf-8")
            buf = io.StringIO()
            with self.assertRaises(EmptyFederatedGraphRefusedError):
                with redirect_stderr(buf):
                    enforce_nonempty_federated_write(
                        target,
                        {"nodes": {}},
                        _build_state_with_missing(["services-api", "services-auth"]),
                        allow_empty=False,
                    )
            err = buf.getvalue()
            self.assertIn("refusing to overwrite", err)
            self.assertIn("services-api", err)
            self.assertIn("services-auth", err)
            # Prior file is byte-identical -- the guard runs before any write.
            self.assertEqual(target.read_text(encoding="utf-8"), prior_text)

    def test_allow_empty_bypasses_guard(self) -> None:
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "graph.json"
            target.write_text(json.dumps({"nodes": {"keep": {}}}), encoding="utf-8")
            # Should not raise.
            enforce_nonempty_federated_write(
                target,
                {"nodes": {}},
                _build_state_with_missing(["services-api"]),
                allow_empty=True,
            )


class MissingChildNamesTest(unittest.TestCase):
    def test_returns_only_non_present_children_sorted(self) -> None:
        children = {
            "z-broken": WorkspaceChildState(
                status="missing", head_sha=None, head_ref=None,
                is_dirty=False, graph_path="z-broken/.weld/graph.json",
                graph_sha256=None, last_seen_utc="t0",
            ),
            "a-good": WorkspaceChildState(
                status="present", head_sha="abc", head_ref="refs/heads/main",
                is_dirty=False, graph_path="a-good/.weld/graph.json",
                graph_sha256="d" * 64, last_seen_utc="t0",
            ),
            "m-empty": WorkspaceChildState(
                status="uninitialized", head_sha=None, head_ref=None,
                is_dirty=False, graph_path="m-empty/.weld/graph.json",
                graph_sha256=None, last_seen_utc="t0",
            ),
        }
        names = missing_child_names(WorkspaceState(children=children))
        self.assertEqual(names, ["m-empty", "z-broken"])


# Layer 5 -- federated discover end-to-end with the guard

class FederatedDiscoverGuardIntegrationTest(unittest.TestCase):
    def _build_seed_workspace(self, root: Path) -> None:
        _init_repo(root / "services" / "api")
        _write_child_graph(root / "services" / "api")
        _write_workspaces(
            root,
            [ChildEntry(name="services-api", path="services/api")],
        )

    def test_guard_refuses_on_zero_node_rewrite(self) -> None:
        with TemporaryDirectory() as tmp:
            root = _init_repo(Path(tmp) / "root")
            self._build_seed_workspace(root)
            target = root / ".weld" / "graph.json"
            # Seed a non-empty federated graph at the canonical write path
            # so the guard's "prior >0" branch is exercised.
            discover(root, incremental=False, output=target)
            self.assertGreater(existing_node_count(target), 0)
            prior_bytes = target.read_bytes()

            # Rename the child away so the next federation pass yields 0
            # nodes. This is the worktree's failure mode without the
            # fallback -- and it is the canonical guard-trigger scenario.
            (root / "services" / "api").rename(root / "services" / "_api_renamed")

            buf = io.StringIO()
            with self.assertRaises(EmptyFederatedGraphRefusedError):
                with redirect_stderr(buf):
                    discover(root, incremental=False, output=target)
            err = buf.getvalue()
            self.assertIn("refusing to overwrite", err)
            self.assertIn("services-api", err)
            # The on-disk graph is byte-identical to the prior write.
            self.assertEqual(target.read_bytes(), prior_bytes)

    def test_allow_empty_bypasses_guard_end_to_end(self) -> None:
        with TemporaryDirectory() as tmp:
            root = _init_repo(Path(tmp) / "root")
            self._build_seed_workspace(root)
            target = root / ".weld" / "graph.json"
            discover(root, incremental=False, output=target)
            (root / "services" / "api").rename(root / "services" / "_api_renamed")
            graph = discover(
                root, incremental=False, output=target, allow_empty=True,
            )
            self.assertEqual(graph.get("nodes"), {})
            # The on-disk file is now the empty graph -- bypass took effect.
            self.assertEqual(existing_node_count(target), 0)


if __name__ == "__main__":
    unittest.main()
