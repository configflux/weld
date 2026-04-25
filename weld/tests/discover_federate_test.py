"""Unit tests for :mod:`weld._discover_federate`.

Covers the cross-repo wiring between ``build_root_meta_graph`` and
``run_resolvers`` introduced for the polyrepo demo: empty-strategy
workspaces are no-ops, present-children-only resolvers produce edges,
and missing/uninitialized children are skipped without crashing the
pass.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from weld._discover_federate import merge_cross_repo_edges
from weld.contract import SCHEMA_VERSION
from weld.cross_repo.base import (
    CrossRepoEdge,
    CrossRepoResolver,
    ResolverContext,
    _REGISTRY,
    register_resolver,
)
from weld.workspace import ChildEntry, UNIT_SEPARATOR, WorkspaceConfig
from weld.workspace_state import WorkspaceChildState, WorkspaceState


# ---------------------------------------------------------------------------
# Test helpers -- keep local so we do not inherit another test module's state.
# ---------------------------------------------------------------------------


def _write_child_graph(
    root: Path,
    rel_path: str,
    nodes: dict[str, dict] | None = None,
) -> None:
    """Write a minimal v1 child graph under ``<root>/<rel_path>/.weld/graph.json``.

    The graph has ``schema_version=1`` (child schema) and is valid JSON
    matching the contract so ``Graph.load()`` succeeds on it.
    """
    weld_dir = root / rel_path / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {"version": SCHEMA_VERSION, "schema_version": 1},
        "nodes": nodes or {},
        "edges": [],
    }
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _state_present(names: list[str]) -> WorkspaceState:
    return WorkspaceState(
        children={
            name: WorkspaceChildState(
                status="present",
                head_sha=None,
                head_ref=None,
                is_dirty=False,
                graph_path=f"children/{name}/.weld/graph.json",
                graph_sha256=None,
                last_seen_utc="2026-04-24T00:00:00+00:00",
            )
            for name in names
        },
    )


def _empty_root_graph() -> dict:
    return {
        "meta": {"version": SCHEMA_VERSION, "schema_version": 2},
        "nodes": {},
        "edges": [],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class EmptyStrategiesIsNoOpTest(unittest.TestCase):
    """A workspace with no cross_repo_strategies leaves edges untouched."""

    def test_returns_graph_unchanged_when_strategies_empty(self) -> None:
        config = WorkspaceConfig(
            children=[ChildEntry(name="c1", path="children/c1")],
            cross_repo_strategies=[],
        )
        state = _state_present(["c1"])
        graph = _empty_root_graph()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_child_graph(root, "children/c1")
            result = merge_cross_repo_edges(root, config, state, graph)
        self.assertEqual(result["edges"], [])


class NoPresentChildrenIsNoOpTest(unittest.TestCase):
    """When no child is ``present`` the merge is a no-op even with resolvers."""

    def test_all_missing_children_produce_no_edges(self) -> None:
        config = WorkspaceConfig(
            children=[ChildEntry(name="c1", path="children/c1")],
            cross_repo_strategies=["service_graph"],
        )
        # Mark c1 as missing so the present-filter drops it.
        state = WorkspaceState(
            children={
                "c1": WorkspaceChildState(
                    status="missing",
                    head_sha=None,
                    head_ref=None,
                    is_dirty=False,
                    graph_path="children/c1/.weld/graph.json",
                    graph_sha256=None,
                    last_seen_utc="2026-04-24T00:00:00+00:00",
                ),
            },
        )
        graph = _empty_root_graph()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = merge_cross_repo_edges(root, config, state, graph)
        self.assertEqual(result["edges"], [])


class ResolverEdgesAreMergedTest(unittest.TestCase):
    """A registered resolver's edges appear in the root graph, sorted + deduped."""

    _NAME = "__discover_federate_test_resolver__"

    @classmethod
    def setUpClass(cls) -> None:
        # Register a fixture resolver that emits a single predictable edge
        # referencing both children it receives. Using a test-local name
        # keeps us out of the workspace validator's allowlist; we bypass
        # the validator by constructing ``WorkspaceConfig`` directly.
        @register_resolver(cls._NAME)
        class _FixtureResolver(CrossRepoResolver):
            name = cls._NAME

            def resolve(self, context: ResolverContext) -> list[CrossRepoEdge]:
                names = sorted(context.children)
                if len(names) < 2:
                    return []
                return [
                    CrossRepoEdge(
                        from_id=f"{names[0]}{UNIT_SEPARATOR}n1",
                        to_id=f"{names[1]}{UNIT_SEPARATOR}n2",
                        type="cross_repo:test",
                        props={"resolver": cls._NAME},
                    ),
                ]

        cls._resolver_cls = _FixtureResolver

    @classmethod
    def tearDownClass(cls) -> None:
        # Clean up the registry so other tests do not see a rogue entry.
        _REGISTRY.pop(cls._NAME, None)

    def test_edges_are_merged_into_root_graph(self) -> None:
        config = WorkspaceConfig(
            children=[
                ChildEntry(name="alpha", path="children/alpha"),
                ChildEntry(name="beta", path="children/beta"),
            ],
            cross_repo_strategies=[self._NAME],
        )
        state = _state_present(["alpha", "beta"])
        graph = _empty_root_graph()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_child_graph(root, "children/alpha")
            _write_child_graph(root, "children/beta")
            result = merge_cross_repo_edges(root, config, state, graph)
        self.assertEqual(len(result["edges"]), 1)
        edge = result["edges"][0]
        self.assertEqual(edge["type"], "cross_repo:test")
        self.assertTrue(edge["from"].startswith("alpha"))
        self.assertTrue(edge["to"].startswith("beta"))

    def test_duplicate_edges_are_deduplicated(self) -> None:
        config = WorkspaceConfig(
            children=[
                ChildEntry(name="alpha", path="children/alpha"),
                ChildEntry(name="beta", path="children/beta"),
            ],
            cross_repo_strategies=[self._NAME, self._NAME],  # run twice
        )
        state = _state_present(["alpha", "beta"])
        graph = _empty_root_graph()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_child_graph(root, "children/alpha")
            _write_child_graph(root, "children/beta")
            result = merge_cross_repo_edges(root, config, state, graph)
        # Two resolver invocations emitting identical edges -> one edge.
        self.assertEqual(len(result["edges"]), 1)


class CorruptChildIsSkippedTest(unittest.TestCase):
    """A child whose graph.json fails to parse is skipped, not crashed."""

    _NAME = "__discover_federate_corrupt_test__"

    @classmethod
    def setUpClass(cls) -> None:
        @register_resolver(cls._NAME)
        class _NoopResolver(CrossRepoResolver):
            name = cls._NAME

            def resolve(self, context: ResolverContext) -> list[CrossRepoEdge]:
                # Touching children[*] would raise on a corrupt child if
                # the caller had not filtered it out. We just observe the
                # set of children made visible to us.
                _ = sorted(context.children)
                return []

        cls._resolver_cls = _NoopResolver

    @classmethod
    def tearDownClass(cls) -> None:
        _REGISTRY.pop(cls._NAME, None)

    def test_corrupt_child_graph_is_skipped(self) -> None:
        config = WorkspaceConfig(
            children=[
                ChildEntry(name="alpha", path="children/alpha"),
                ChildEntry(name="broken", path="children/broken"),
            ],
            cross_repo_strategies=[self._NAME],
        )
        state = _state_present(["alpha", "broken"])
        graph = _empty_root_graph()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_child_graph(root, "children/alpha")
            # Write a non-JSON blob to trigger a parse failure. The
            # federator's _load_present_child_graph should log + skip.
            weld_dir = root / "children" / "broken" / ".weld"
            weld_dir.mkdir(parents=True, exist_ok=True)
            (weld_dir / "graph.json").write_text("{not json", encoding="utf-8")
            result = merge_cross_repo_edges(root, config, state, graph)
        self.assertEqual(result["edges"], [])


if __name__ == "__main__":
    unittest.main()
