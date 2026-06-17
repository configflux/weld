"""Tests for ``wd discover --recurse``: cascade discovery into children.

Verifies that ``--recurse`` discovers each present child in-process,
writes the child graph, rebuilds the root meta-graph reflecting the fresh
child state, and skips missing/uninitialized children gracefully.
"""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from weld.contract import SCHEMA_VERSION
from weld.discover import discover
from weld.workspace import ChildEntry, WorkspaceConfig, dump_workspaces_yaml


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
    readme = repo_root / "README.md"
    readme.write_text("# fixture\n", encoding="utf-8")
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


def _write_discover_yaml(repo_root: Path) -> None:
    """Write a minimal discover.yaml so child discovery succeeds."""
    weld_dir = repo_root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "discover.yaml").write_text(
        "sources: []\n", encoding="utf-8",
    )


def _write_workspaces(root: Path, children: list[ChildEntry]) -> WorkspaceConfig:
    config = WorkspaceConfig(children=children, cross_repo_strategies=[])
    dump_workspaces_yaml(config, root / ".weld" / "workspaces.yaml")
    return config


class DiscoverRecurseTest(unittest.TestCase):
    """Acceptance tests for ``discover(recurse=True)``."""

    def test_recurse_discovers_present_children_and_rebuilds_root(self) -> None:
        """Present children get discovered; root meta-graph reflects them."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            child_a = _init_repo(root / "services" / "api")
            child_b = _init_repo(root / "services" / "auth")
            _write_child_graph(child_a)
            _write_child_graph(child_b)
            _write_discover_yaml(child_a)
            _write_discover_yaml(child_b)
            _write_workspaces(root, [
                ChildEntry(name="services-api", path="services/api"),
                ChildEntry(name="services-auth", path="services/auth"),
            ])

            graph = discover(root, incremental=False, recurse=True)

            self.assertEqual(graph["meta"]["schema_version"], 2)
            self.assertIn("repo:services-api", graph["nodes"])
            self.assertIn("repo:services-auth", graph["nodes"])

    def test_recurse_skips_missing_children(self) -> None:
        """Missing children are skipped without error."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            child_a = _init_repo(root / "services" / "api")
            _write_child_graph(child_a)
            _write_discover_yaml(child_a)
            _write_workspaces(root, [
                ChildEntry(name="services-api", path="services/api"),
                ChildEntry(name="libs-missing", path="libs/missing"),
            ])

            graph = discover(root, incremental=False, recurse=True)

            self.assertIn("repo:services-api", graph["nodes"])
            self.assertNotIn("repo:libs-missing", graph["nodes"])

    def test_recurse_updates_child_graph_on_disk(self) -> None:
        """After recurse, child .weld/graph.json is refreshed."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = _init_repo(root / "app")
            _write_child_graph(child)
            _write_discover_yaml(child)
            _write_workspaces(root, [
                ChildEntry(name="app", path="app"),
            ])

            child_graph_path = child / ".weld" / "graph.json"
            old_content = child_graph_path.read_text(encoding="utf-8")

            discover(root, incremental=False, recurse=True)

            new_content = child_graph_path.read_text(encoding="utf-8")
            # The child graph must have been overwritten by the in-process
            # discovery run -- the content should differ from the stub.
            self.assertNotEqual(old_content, new_content)
            new_data = json.loads(new_content)
            self.assertIn("meta", new_data)
            self.assertIn("nodes", new_data)
            self.assertIn("edges", new_data)

    def test_recurse_false_does_not_cascade(self) -> None:
        """Without recurse, children are not re-discovered."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = _init_repo(root / "app")
            # Write a minimal child graph but no discover.yaml -- if recurse
            # tried to discover, it would fail or produce a different result.
            _write_child_graph(child)
            _write_workspaces(root, [
                ChildEntry(name="app", path="app"),
            ])

            graph = discover(root, incremental=False, recurse=False)

            # Root graph still built from existing child state.
            self.assertIn("repo:app", graph["nodes"])

    def test_recurse_on_non_workspace_is_noop(self) -> None:
        """recurse=True on a non-workspace root just runs single-repo."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            _write_discover_yaml(root)

            graph = discover(root, incremental=False, recurse=True)

            self.assertEqual(graph["meta"]["schema_version"], 1)
            for nid in graph.get("nodes", {}):
                self.assertFalse(nid.startswith("repo:"), f"unexpected {nid}")

    def test_recurse_rebuilds_state_after_child_discovery(self) -> None:
        """Ledger is rebuilt after recurse so uninitialized->present transitions."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = _init_repo(root / "svc")
            # Child exists as git repo but has no graph yet (uninitialized).
            # Add discover.yaml so discovery can succeed.
            _write_discover_yaml(child)
            _write_workspaces(root, [
                ChildEntry(name="svc", path="svc"),
            ])

            # Without recurse: child is uninitialized, no repo node.
            graph_no_recurse = discover(root, incremental=False, recurse=False)
            self.assertNotIn("repo:svc", graph_no_recurse["nodes"])

            # With recurse: child gets discovered, then root sees it as present.
            graph_recurse = discover(root, incremental=False, recurse=True)
            self.assertIn("repo:svc", graph_recurse["nodes"])


class RecurseChildrenNamesRestrictionTest(unittest.TestCase):
    """``recurse_children(names=...)`` visits only the named subset.

    ADR 0066 part 3 step 4: auto-recurse-on-read refreshes *only* the stale
    subset, not every present child. The selector passes the stale child
    names; ``recurse_children`` must skip every other present child so a
    one-child edit refreshes one child (proportional refresh).
    """

    def test_names_restricts_to_named_subset(self) -> None:
        from weld._discover_recurse import recurse_children
        from weld.workspace_state import build_workspace_state

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            child_a = _init_repo(root / "svc-a")
            child_b = _init_repo(root / "svc-b")
            _write_child_graph(child_a)
            _write_child_graph(child_b)
            _write_discover_yaml(child_a)
            _write_discover_yaml(child_b)
            config = _write_workspaces(root, [
                ChildEntry(name="svc-a", path="svc-a"),
                ChildEntry(name="svc-b", path="svc-b"),
            ])
            state = build_workspace_state(root, config)

            graph_a = child_a / ".weld" / "graph.json"
            graph_b = child_b / ".weld" / "graph.json"
            before_a = graph_a.read_text(encoding="utf-8")
            before_b = graph_b.read_text(encoding="utf-8")

            result = recurse_children(
                root, config, state, incremental=False, names={"svc-a"},
            )

            # Only svc-a was discovered; svc-b's graph is byte-untouched.
            self.assertEqual(result.discovered, ["svc-a"])
            self.assertNotIn("svc-b", result.discovered)
            self.assertNotEqual(before_a, graph_a.read_text(encoding="utf-8"))
            self.assertEqual(before_b, graph_b.read_text(encoding="utf-8"))

    def test_names_none_visits_all_present(self) -> None:
        """names=None preserves the existing 'visit every present child'."""
        from weld._discover_recurse import recurse_children
        from weld.workspace_state import build_workspace_state

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            child_a = _init_repo(root / "svc-a")
            child_b = _init_repo(root / "svc-b")
            _write_child_graph(child_a)
            _write_child_graph(child_b)
            _write_discover_yaml(child_a)
            _write_discover_yaml(child_b)
            config = _write_workspaces(root, [
                ChildEntry(name="svc-a", path="svc-a"),
                ChildEntry(name="svc-b", path="svc-b"),
            ])
            state = build_workspace_state(root, config)

            result = recurse_children(root, config, state, incremental=False)
            self.assertEqual(sorted(result.discovered), ["svc-a", "svc-b"])


class DiscoverRecurseFromWorktreeTest(unittest.TestCase):
    """ADR 0028 §1: ``wd discover --recurse`` from a linked git worktree
    must resolve each child via the main checkout (where the child repo
    actually lives), not via ``root / child.path`` which does not exist
    in the linked worktree.
    """

    def test_recurse_uses_resolve_child_root_from_a_linked_worktree(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            main = _init_repo(tmp_path / "main")
            child = _init_repo(main / "services" / "api")
            _write_child_graph(child)
            _write_discover_yaml(child)
            _write_workspaces(main, [
                ChildEntry(name="services-api", path="services/api"),
            ])
            # Commit so the worktree branch sees the federation registry.
            _git(main, "add", "-A")
            _git(main, "commit", "-q", "-m", "seed federation")

            # Add a linked worktree on a fresh branch. The child repo's
            # ``.git`` lives only at the main checkout: a fresh linked
            # worktree never carries nested git repositories.
            wt = tmp_path / "wt"
            _git(main, "worktree", "add", "-q", str(wt), "-b", "feature")
            self.assertFalse((wt / "services" / "api" / ".git").exists())

            # Recurse from the worktree must still resolve the child via
            # the main checkout (resolve_child_root fallback) and discover
            # it; without the fix the recurse loop computes the worktree
            # path and fails to refresh, so the root meta-graph never
            # gains a ``repo:services-api`` node.
            graph = discover(wt, incremental=False, recurse=True)
            self.assertIn("repo:services-api", graph["nodes"])


def _seed_fastapi_child(child_root: Path) -> None:
    """Write a ``services/<name>``-style FastAPI child (app + routers/).

    The app is instantiated in ``src/app.py`` while the ``APIRouter`` lives
    one directory down in ``src/routers/tokens.py`` -- the layout that made
    ``wd init`` wire the fastapi strategy at the app-file glob and drop the
    route. ``wd init`` is then run so the child gets a *generated*
    discover.yaml, exercising the real config-generation path the bug lives
    in (not a hand-written yaml that already points at the routers dir).
    """
    src = child_root / "src"
    routers = src / "routers"
    routers.mkdir(parents=True, exist_ok=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "app.py").write_text(
        "from fastapi import FastAPI\n"
        "from .routers.tokens import router as tokens_router\n"
        "app = FastAPI(title='auth')\n"
        "app.include_router(tokens_router)\n",
        encoding="utf-8",
    )
    (routers / "__init__.py").write_text("", encoding="utf-8")
    (routers / "tokens.py").write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "@router.post('/tokens')\n"
        "def create_token() -> dict:\n"
        "    return {'value': 'tok'}\n",
        encoding="utf-8",
    )
    from weld.init import main as init_main
    init_main([str(child_root)])
    _git(child_root, "add", "-A")
    _git(child_root, "commit", "-q", "-m", "fastapi child")


def _route_nodes(graph: dict) -> set[str]:
    return {k for k in graph.get("nodes", {}) if k.startswith("route:")}


class RecurseFastapiEquivalenceTest(unittest.TestCase):
    """Recurse must produce the same child graph as a standalone discover.

    Regression for the polyrepo dogfood gap: ``wd discover --recurse``
    rebuilt a child *without* its FastAPI ``route:`` nodes while a
    standalone ``wd discover --full <child>`` kept them. The divergence was
    a ``wd init`` glob-selection bug, so the child's *generated*
    discover.yaml is used here (via :func:`_seed_fastapi_child`).
    """

    def test_recurse_child_matches_standalone_for_fastapi_routes(self) -> None:
        from weld.discover import _discover_single_repo

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = _init_repo(root / "services" / "auth")
            _seed_fastapi_child(child)
            _write_workspaces(root, [
                ChildEntry(name="services-auth", path="services/auth"),
            ])

            # Standalone full discover of the child (the path users reach
            # for to "restore" the routes).
            standalone = _discover_single_repo(child, incremental=False)
            standalone_routes = _route_nodes(standalone)
            self.assertEqual(
                standalone_routes, {"route:POST:/tokens"},
                "standalone discover must extract the FastAPI route",
            )

            # Recurse from the root rebuilds the child graph on disk.
            discover(root, incremental=False, recurse=True)
            recurse_child = json.loads(
                (child / ".weld" / "graph.json").read_text(encoding="utf-8"),
            )

            # The acceptance bar: recurse output is equivalent to standalone
            # for the same child state -- asserted generally on the node set,
            # and specifically on the route nodes that regressed.
            self.assertEqual(
                _route_nodes(recurse_child), standalone_routes,
                "recurse must preserve the same FastAPI route nodes as a "
                "standalone discover of the same child",
            )
            self.assertEqual(
                set(recurse_child["nodes"]), set(standalone["nodes"]),
                "recurse child node set must equal standalone node set",
            )


if __name__ == "__main__":
    unittest.main()
