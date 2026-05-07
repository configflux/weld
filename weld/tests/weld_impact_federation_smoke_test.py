"""Federation smoke test for ``weld.impact_core.impact``.

Layer A had a deferred follow-up (bd-...-tv99): exercise ``impact()``
against a :class:`weld.federation.FederatedGraph` with at least one child
repo, prove the BFS picks up cross-repo edges from the root meta-graph,
and assert a cross-repo dependent appears in ``transitive_dependents``.

This is a *contract* smoke -- a regression that drops cross-repo edges
from ``FederatedGraph.dump()['edges']`` (or that breaks reverse-BFS
visibility into prefixed-id edges) must turn this test red. It deliberately
only exercises the public seam (``impact(graph, seeds=...)``); the helper
internals stay covered by ``weld_impact_test.py`` and helper-level
checks in ``weld_impact_cli_test.py``.

Fixture style follows ``weld/tests/weld_federation_test.py`` and
``weld/tests/_discover_federate_origin_fixtures.py`` -- a real ``git
init`` per child plus a hand-shaped ``.weld/graph.json`` so the smoke
fails for the same reasons the production ``FederatedGraph`` would.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


_REPO_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


from weld.contract import SCHEMA_VERSION  # noqa: E402  (runs after sys.path tweak)
from weld.federation import FederatedGraph, prefix_node_id  # noqa: E402
from weld.impact_core import impact  # noqa: E402
from weld.workspace import ChildEntry, WorkspaceConfig, dump_workspaces_yaml  # noqa: E402

_TS = "2026-05-04T00:00:00+00:00"


def _git(repo_root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=True,
        env={"LC_ALL": "C", "PATH": "/usr/bin:/usr/local/bin:/bin"},
    )


def _init_repo(repo_root: Path) -> Path:
    """Initialise *repo_root* as a real git repo with one commit on ``main``.

    ``FederatedGraph._load_child`` rejects any registered child whose
    directory lacks a ``.git`` entry, so a real ``git init`` is the
    minimum viable fixture.
    """
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-q", "-b", "main")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Weld Test")
    (repo_root / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "commit", "-q", "-m", "init")
    return repo_root


def _write_graph(repo_root: Path, payload: dict) -> None:
    weld_dir = repo_root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _graph_payload(
    nodes: dict,
    edges: list[dict] | None = None,
    *,
    schema_version: int = 1,
) -> dict:
    return {
        "meta": {
            "version": SCHEMA_VERSION,
            "updated_at": _TS,
            "schema_version": schema_version,
        },
        "nodes": nodes,
        "edges": edges or [],
    }


def _file_node(file_path: str, *, label: str | None = None) -> dict:
    return {
        "type": "file",
        "label": label or file_path,
        "props": {"file": file_path, "language": "python"},
    }


def _build_federation_workspace(root: Path) -> dict:
    """Materialise a tiny parent root + 1 child repo workspace.

    Returns a dict of well-known ids the test can assert against:
    ``seed`` (prefixed child node), ``direct`` (parent file directly
    depending on the seed), and ``transitive`` (parent file two hops
    away from the seed via ``direct``).
    """
    child_a = _init_repo(root / "child-a")
    # Child carries the leaf node only; its ``graph.json`` has no edges
    # because the cross-repo dependency lives in the root meta-graph.
    _write_graph(
        child_a,
        _graph_payload(
            {
                "file:src/leaf.py": _file_node("src/leaf.py", label="leaf"),
            }
        ),
    )

    seed_id = prefix_node_id("child-a", "file:src/leaf.py")
    direct_id = "file:parent/dep.py"
    transitive_id = "file:parent/top.py"

    # Root meta-graph: one ``repo:*`` node per child + the cross-repo
    # edges. ``schema_version=2`` is mandatory for federated roots.
    root_nodes = {
        "repo:child-a": {
            "type": "repo",
            "label": "child-a",
            "props": {"path": "child-a"},
        },
        direct_id: _file_node("parent/dep.py", label="dep"),
        transitive_id: _file_node("parent/top.py", label="top"),
    }
    root_edges = [
        # Cross-repo: parent file directly depends on child's leaf.
        {
            "from": direct_id,
            "to": seed_id,
            "type": "depends_on",
            "props": {},
        },
        # Same-repo (root) chain that yields a transitive dependent at
        # hop 2 from the perspective of the child seed.
        {
            "from": transitive_id,
            "to": direct_id,
            "type": "depends_on",
            "props": {},
        },
    ]
    _write_graph(root, _graph_payload(root_nodes, root_edges, schema_version=2))

    config = WorkspaceConfig(
        children=[ChildEntry(name="child-a", path="child-a")],
        cross_repo_strategies=[],
    )
    dump_workspaces_yaml(config, root / ".weld" / "workspaces.yaml")

    return {
        "seed": seed_id,
        "direct": direct_id,
        "transitive": transitive_id,
    }


class ImpactFederationSmokeTest(unittest.TestCase):
    """``impact()`` traverses cross-repo edges in a FederatedGraph.

    The test fails if ``FederatedGraph.dump()['edges']`` ever drops the
    root meta-graph's cross-repo edges, or if the BFS in
    :func:`weld.impact_core._reverse_bfs` regresses against prefixed
    ``\\x1f``-encoded ids.
    """

    def test_cross_repo_dependent_appears_in_transitive_dependents(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ids = _build_federation_workspace(root)

            graph = FederatedGraph(root)
            result = impact(graph, seeds=[ids["seed"]], depth=3)

            direct_ids = {entry["id"] for entry in result["direct_dependents"]}
            transitive_ids = {entry["id"] for entry in result["transitive_dependents"]}

            # Direct: cross-repo, hop 1 -- proves dump() exposes the
            # cross-repo edge from the root meta-graph at all.
            self.assertIn(
                ids["direct"], direct_ids,
                msg=(
                    "cross-repo direct dependent missing -- FederatedGraph "
                    "may have dropped the root meta-graph edge"
                ),
            )
            # Transitive: hop 2, the contract this smoke is named for.
            self.assertIn(
                ids["transitive"], transitive_ids,
                msg=(
                    "cross-repo transitive dependent missing -- BFS "
                    "regressed against prefixed (cross-repo) seed ids"
                ),
            )
            # Seed must not appear as its own dependent.
            self.assertNotIn(ids["seed"], direct_ids | transitive_ids)

    def test_envelope_is_deterministic_across_two_invocations(self) -> None:
        """Determinism regression must survive the federation seam.

        Mirrors the locked contract in
        ``weld_impact_cli_test.DeterminismRegressionTest``. Two
        invocations on the *same* on-disk state must produce
        byte-identical envelopes when serialised with sorted keys.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ids = _build_federation_workspace(root)

            graph_a = FederatedGraph(root)
            graph_b = FederatedGraph(root)
            result_a = impact(graph_a, seeds=[ids["seed"]], depth=3)
            result_b = impact(graph_b, seeds=[ids["seed"]], depth=3)

            self.assertEqual(
                json.dumps(result_a, sort_keys=True, ensure_ascii=True),
                json.dumps(result_b, sort_keys=True, ensure_ascii=True),
            )


if __name__ == "__main__":
    unittest.main()
