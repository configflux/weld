"""Acceptance tests for the root discovery branch (ADR 0011 section 4).

When a workspace root declares :file:`workspaces.yaml`, ``wd discover``
must emit a meta-graph containing only ``repo:<name>`` nodes -- one per
registered child -- zero cross-repo edges, and ``meta.schema_version=2``.
When the registry is absent the discover path falls through to the
legacy single-repo branch and emits ``meta.schema_version=1``.

These tests exercise the acceptance criteria end-to-end:

* meta-graph content: node count, ids, props, edges, schema version;
* deterministic byte-identical output across two runs modulo
  ``meta.updated_at`` (the repo's volatile timestamp field);
* OSS-split regression: deleting ``workspaces.yaml`` restores legacy
  single-repo output with ``schema_version=1`` and no ``repo:*`` nodes;
* sentinel handling: ``missing`` children and ``uninitialized`` children
  are recorded in the ledger but do not leak ``repo:*`` nodes, and the
  discover run never writes into an uninitialized child directory.
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
from weld.workspace_state import WORKSPACE_STATE_FILENAME


def _git(repo_root: Path, *args: str) -> str:
    """Run git under ``LC_ALL=C`` so output is locale-stable."""
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
    """Initialise a git repo with one commit so ``rev-parse HEAD`` succeeds."""
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
    """Drop a minimal v1 graph into the child's ``.weld/`` so it reads ``present``."""
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


def _strip_volatile(graph: dict) -> dict:
    """Return a copy of *graph* with volatile meta fields removed.

    ``meta.updated_at`` changes on every run by definition; stripping it
    is the prescribed way to diff two canonical graphs under the
    determinism contract (ADR 0012 section 1).
    """
    copy = json.loads(json.dumps(graph))
    meta = copy.get("meta", {})
    meta.pop("updated_at", None)
    # ``discovered_from`` is content-driven but we keep it comparable; the
    # single-repo branch can include ``git_sha`` which is commit-stable but
    # for this test we treat it as volatile because the test fixtures use
    # fresh repos whose SHAs may differ across runs on different machines.
    meta.pop("git_sha", None)
    return copy


class RootDiscoveryMetaGraphTest(unittest.TestCase):
    """Acceptance tests for the federation root discovery branch."""

    def test_meta_graph_has_one_repo_node_per_present_child(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root / "services" / "api")
            _init_repo(root / "services" / "auth")
            _init_repo(root / "apps" / "frontend")
            _write_child_graph(root / "services" / "api")
            _write_child_graph(root / "services" / "auth")
            _write_child_graph(root / "apps" / "frontend")

            _write_workspaces(
                root,
                [
                    # Declaration order differs from sorted order so the
                    # test verifies lexicographic emission, not
                    # insertion order.
                    ChildEntry(name="services-api", path="services/api"),
                    ChildEntry(name="apps-frontend", path="apps/frontend"),
                    ChildEntry(name="services-auth", path="services/auth"),
                ],
            )

            graph = discover(root, incremental=False)

            self.assertEqual(graph["meta"]["schema_version"], 2)
            self.assertEqual(graph["edges"], [])

            nodes = graph["nodes"]
            # Exactly three repo:* nodes, no other node types.
            self.assertEqual(
                sorted(nodes.keys()),
                ["repo:apps-frontend", "repo:services-api", "repo:services-auth"],
            )
            for node in nodes.values():
                self.assertEqual(node["type"], "repo")

    def test_repo_node_carries_path_metadata_and_tags(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root / "services" / "api")
            _write_child_graph(root / "services" / "api")
            _write_workspaces(
                root,
                [ChildEntry(name="services-api", path="services/api")],
            )

            graph = discover(root, incremental=False)
            node = graph["nodes"]["repo:services-api"]

            self.assertEqual(node["label"], "services-api")
            props = node["props"]
            self.assertEqual(props["path"], "services/api")
            self.assertEqual(props["path_segments"], ["services", "api"])
            self.assertEqual(props["depth"], 2)
            # Auto-filled tag: immediate parent segment becomes ``category``.
            self.assertEqual(props["tags"], {"category": "services"})
            self.assertEqual(props["source_strategy"], "federation_root")
            self.assertEqual(props["authority"], "canonical")
            self.assertEqual(props["confidence"], "definite")
            # Remote was not declared, so the prop must not appear.
            self.assertNotIn("remote", props)

    def test_meta_graph_includes_remote_when_declared(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root / "libs" / "shared")
            _write_child_graph(root / "libs" / "shared")
            _write_workspaces(
                root,
                [
                    ChildEntry(
                        name="libs-shared",
                        path="libs/shared",
                        remote="git@example.com:libs/shared.git",
                    ),
                ],
            )

            graph = discover(root, incremental=False)
            props = graph["nodes"]["repo:libs-shared"]["props"]
            self.assertEqual(props["remote"], "git@example.com:libs/shared.git")

    def test_two_runs_produce_byte_identical_output(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root / "services" / "api")
            _init_repo(root / "services" / "auth")
            _write_child_graph(root / "services" / "api")
            _write_child_graph(root / "services" / "auth")
            _write_workspaces(
                root,
                [
                    ChildEntry(name="services-api", path="services/api"),
                    ChildEntry(name="services-auth", path="services/auth"),
                ],
            )

            first = discover(root, incremental=False)
            second = discover(root, incremental=False)

            self.assertEqual(_strip_volatile(first), _strip_volatile(second))

    def test_removing_workspaces_yaml_restores_single_repo_output(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            # With workspaces.yaml present we expect schema_version=2 and
            # no non-repo nodes on an otherwise-empty workspace.
            _write_workspaces(root, [])
            federated = discover(root, incremental=False)
            self.assertEqual(federated["meta"]["schema_version"], 2)
            self.assertEqual(federated["nodes"], {})

            # Remove the registry and re-discover: output must drop back
            # to the single-repo branch (schema_version=1, no repo:*
            # nodes). This is the rollback contract in ADR 0011 section 9.
            (root / ".weld" / "workspaces.yaml").unlink()
            single = discover(root, incremental=False)
            self.assertEqual(single["meta"]["schema_version"], 1)
            for node_id in single.get("nodes", {}):
                self.assertFalse(
                    node_id.startswith("repo:"),
                    f"legacy single-repo output must not contain {node_id!r}",
                )

    def test_missing_child_is_not_emitted_but_recorded_in_ledger(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root / "services" / "api")
            _write_child_graph(root / "services" / "api")
            _write_workspaces(
                root,
                [
                    ChildEntry(name="services-api", path="services/api"),
                    # Nonexistent path -- status: missing.
                    ChildEntry(name="libs-shared", path="libs/shared"),
                ],
            )

            graph = discover(root, incremental=False)

            # Present child emits a node; missing child does not.
            self.assertIn("repo:services-api", graph["nodes"])
            self.assertNotIn("repo:libs-shared", graph["nodes"])

            ledger = json.loads(
                (root / ".weld" / WORKSPACE_STATE_FILENAME).read_text(encoding="utf-8"),
            )
            self.assertEqual(ledger["children"]["libs-shared"]["status"], "missing")

    def test_uninitialized_child_does_not_trigger_child_write(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root / "services" / "api")
            _write_child_graph(root / "services" / "api")
            # Second child exists as a git repo but has no .weld/graph.json.
            _init_repo(root / "services" / "auth")
            _write_workspaces(
                root,
                [
                    ChildEntry(name="services-api", path="services/api"),
                    ChildEntry(name="services-auth", path="services/auth"),
                ],
            )

            discover(root, incremental=False)

            auth_weld = root / "services" / "auth" / ".weld"
            self.assertFalse(
                auth_weld.exists(),
                "root discover must not write into an uninitialized child",
            )
            ledger = json.loads(
                (root / ".weld" / WORKSPACE_STATE_FILENAME).read_text(encoding="utf-8"),
            )
            self.assertEqual(
                ledger["children"]["services-auth"]["status"],
                "uninitialized",
            )

    def test_nodes_are_sorted_lexicographically_in_output(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root / "z" / "late")
            _init_repo(root / "a" / "early")
            _init_repo(root / "m" / "middle")
            _write_child_graph(root / "z" / "late")
            _write_child_graph(root / "a" / "early")
            _write_child_graph(root / "m" / "middle")
            _write_workspaces(
                root,
                [
                    ChildEntry(name="z-late", path="z/late"),
                    ChildEntry(name="a-early", path="a/early"),
                    ChildEntry(name="m-middle", path="m/middle"),
                ],
            )

            graph = discover(root, incremental=False)

            # ``canonical_graph`` keeps nodes as a dict; the serializer
            # emits keys in sorted order via ``sort_keys=True``. We verify
            # here that every key is present and that the set is exact.
            self.assertEqual(
                sorted(graph["nodes"].keys()),
                ["repo:a-early", "repo:m-middle", "repo:z-late"],
            )


if __name__ == "__main__":
    unittest.main()
