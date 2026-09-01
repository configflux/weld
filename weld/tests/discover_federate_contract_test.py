"""What ``merge_cross_repo_edges`` writes, and what it refuses to (ADR 0137 ss4).

Two obligations, and they pull in opposite directions, which is why they are
tested together.

*Drop what does not resolve.* A resolver edge whose endpoints name no node is
unreachable by every reader, so keeping it buys nothing; but one buggy
resolver must not sink ``wd discover`` either, so the edge goes and a warning
attributed to the resolver stays.

*Record that the pass happened.* The stamp is the fact no reader could
previously observe -- and the case it exists for is the **zero-edge** run,
where the old "no edges, return early" shortcut skipped it and left "no
cross-repo edge points here" indistinguishable from "no resolver ever looked".
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory

from weld._discover_federate import (
    DROPPED_EDGE_WARNING_CAP,
    merge_cross_repo_edges,
)
from weld.contract import SCHEMA_VERSION
from weld.cross_repo.base import (
    CrossRepoEdge,
    CrossRepoResolver,
    ResolverContext,
    _REGISTRY,
    register_resolver,
)
from weld.tests._federation_id_fixtures import CORRUPT, MISSING, write_child
from weld.workspace import ChildEntry, UNIT_SEPARATOR as SEP, WorkspaceConfig
from weld.workspace_state import WorkspaceChildState, WorkspaceState

_RESOLVER = "__discover_federate_contract_test__"

#: Edges the fixture resolver returns on its next invocation. Set per test.
_PENDING: list[CrossRepoEdge] = []


def _state(names: tuple[str, ...], status: str = "present") -> WorkspaceState:
    return WorkspaceState(
        children={
            name: WorkspaceChildState(
                status=status,
                head_sha=None,
                head_ref=None,
                is_dirty=False,
                graph_path=f"{name}/.weld/graph.json",
                graph_sha256=None,
                last_seen_utc="2026-08-30T00:00:00+00:00",
            )
            for name in names
        },
    )


def _config(names: tuple[str, ...], *, strategies: tuple[str, ...]) -> WorkspaceConfig:
    return WorkspaceConfig(
        children=[ChildEntry(name=name, path=name) for name in names],
        cross_repo_strategies=list(strategies),
    )


def _root_graph(repo_nodes: tuple[str, ...] = ()) -> dict:
    return {
        "meta": {"version": SCHEMA_VERSION, "schema_version": 2},
        "nodes": {
            f"repo:{name}": {
                "type": "repo", "label": name, "props": {"path": name},
            }
            for name in repo_nodes
        },
        "edges": [],
    }


def _edge(from_id: str, to_id: str, **props: object) -> CrossRepoEdge:
    return CrossRepoEdge(
        from_id=from_id,
        to_id=to_id,
        type="cross_repo:depends_on",
        props=dict(props),
    )


class _MergeCase(unittest.TestCase):
    """Registers the fixture resolver and runs one merge on a real tree."""

    @classmethod
    def setUpClass(cls) -> None:
        @register_resolver(_RESOLVER)
        class _FixtureResolver(CrossRepoResolver):
            name = _RESOLVER

            def resolve(self, context: ResolverContext) -> list[CrossRepoEdge]:
                return list(_PENDING)

    @classmethod
    def tearDownClass(cls) -> None:
        _REGISTRY.pop(_RESOLVER, None)

    def tearDown(self) -> None:
        _PENDING.clear()

    def merge(
        self,
        tmp: str,
        *,
        children: tuple[tuple[str, str], ...],
        registered: tuple[str, ...] | None = None,
        repo_nodes: tuple[str, ...] = (),
        strategies: tuple[str, ...] = (_RESOLVER,),
    ) -> tuple[dict, str]:
        """Lay out *children* as ``(name, state)`` and return ``(graph, stderr)``."""
        root = Path(tmp)
        for name, state in children:
            write_child(root, name, state=state, node_ids=("n1", "n2"))
        names = registered or tuple(name for name, _ in children)
        graph = _root_graph(repo_nodes)
        err = io.StringIO()
        with redirect_stderr(err):
            graph = merge_cross_repo_edges(
                root,
                _config(names, strategies=strategies),
                _state(names),
                graph,
            )
        return graph, err.getvalue()


class ResolvableEdgesSurviveTest(_MergeCase):
    def test_edge_between_two_child_nodes_is_merged_and_stamped(self) -> None:
        _PENDING.append(_edge(f"alpha{SEP}n1", f"beta{SEP}n2"))
        with TemporaryDirectory() as tmp:
            graph, err = self.merge(
                tmp,
                children=(("alpha", "present"), ("beta", "present")),
                repo_nodes=("alpha", "beta"),
            )
        self.assertEqual(len(graph["edges"]), 1)
        self.assertEqual(
            graph["meta"]["cross_repo"],
            {
                "strategies": [_RESOLVER],
                "resolved_children": ["alpha", "beta"],
                "edges": 1,
                "dropped": 0,
            },
        )
        self.assertEqual(err, "")

    def test_repo_level_endpoints_resolve_against_the_root(self) -> None:
        _PENDING.append(_edge("repo:alpha", "repo:beta"))
        with TemporaryDirectory() as tmp:
            graph, _err = self.merge(
                tmp,
                children=(("alpha", "present"), ("beta", "present")),
                repo_nodes=("alpha", "beta"),
            )
        self.assertEqual(len(graph["edges"]), 1)
        self.assertEqual(graph["meta"]["cross_repo"]["dropped"], 0)


class UnresolvableEdgesAreDroppedTest(_MergeCase):
    def test_the_hybrid_endpoint_shape_is_dropped_and_warned(self) -> None:
        # The N1 shape: a root-minted id namespaced into a child. No reader
        # can resolve it, so it never reaches the file.
        _PENDING.append(
            _edge(
                f"alpha{SEP}repo:alpha",
                f"beta{SEP}repo:beta",
                source_strategy="package_graph",
            )
        )
        with TemporaryDirectory() as tmp:
            graph, err = self.merge(
                tmp,
                children=(("alpha", "present"), ("beta", "present")),
                repo_nodes=("alpha", "beta"),
            )
        self.assertEqual(graph["edges"], [])
        self.assertEqual(graph["meta"]["cross_repo"]["edges"], 0)
        self.assertEqual(graph["meta"]["cross_repo"]["dropped"], 1)
        self.assertIn("package_graph", err)
        self.assertIn("dropping cross-repo edge", err)

    def test_a_repo_id_for_an_unminted_child_is_dropped(self) -> None:
        # A resolver that reads workspaces.yaml itself can name a registered
        # child the root never minted a node for, which is why the endpoint is
        # classified against the root graph rather than the resolver's inputs.
        _PENDING.append(_edge("repo:alpha", "repo:gone"))
        with TemporaryDirectory() as tmp:
            graph, err = self.merge(
                tmp,
                children=(("alpha", "present"), ("gone", MISSING)),
                repo_nodes=("alpha",),
            )
        self.assertEqual(graph["edges"], [])
        self.assertEqual(graph["meta"]["cross_repo"]["dropped"], 1)
        self.assertIn("repo:gone", err)

    def test_warnings_are_capped_per_resolver_with_a_remainder_line(self) -> None:
        extra = 2
        for i in range(DROPPED_EDGE_WARNING_CAP + extra):
            _PENDING.append(
                _edge(
                    f"alpha{SEP}missing{i}",
                    f"beta{SEP}n2",
                    source_strategy="package_graph",
                )
            )
        with TemporaryDirectory() as tmp:
            graph, err = self.merge(
                tmp,
                children=(("alpha", "present"), ("beta", "present")),
                repo_nodes=("alpha", "beta"),
            )
        self.assertEqual(
            graph["meta"]["cross_repo"]["dropped"],
            DROPPED_EDGE_WARNING_CAP + extra,
        )
        individual = [
            line for line in err.splitlines() if "dropping cross-repo edge" in line
        ]
        self.assertEqual(len(individual), DROPPED_EDGE_WARNING_CAP)
        self.assertIn(f"and {extra} more unresolvable", err)

    def test_an_unattributed_edge_falls_back_to_its_type(self) -> None:
        _PENDING.append(_edge(f"alpha{SEP}nope", f"beta{SEP}n2"))
        with TemporaryDirectory() as tmp:
            _graph, err = self.merge(
                tmp,
                children=(("alpha", "present"), ("beta", "present")),
                repo_nodes=("alpha", "beta"),
            )
        self.assertIn("cross_repo:depends_on", err)


class TheStampRecordsThatResolversRanTest(_MergeCase):
    def test_a_zero_edge_run_is_still_stamped(self) -> None:
        # The whole point: without this, "no cross-repo edge points at this
        # repo" cannot be told from "no resolver ever looked".
        with TemporaryDirectory() as tmp:
            graph, _err = self.merge(
                tmp,
                children=(("alpha", "present"), ("beta", "present")),
                repo_nodes=("alpha", "beta"),
            )
        self.assertEqual(
            graph["meta"]["cross_repo"],
            {
                "strategies": [_RESOLVER],
                "resolved_children": ["alpha", "beta"],
                "edges": 0,
                "dropped": 0,
            },
        )

    def test_resolved_children_is_the_input_set_not_the_producers(self) -> None:
        # beta yields no edge; it was still read, and the stamp has to say so
        # or "nothing was available" and "nothing was found" collapse together.
        _PENDING.append(_edge("repo:alpha", f"alpha{SEP}n1"))
        with TemporaryDirectory() as tmp:
            graph, _err = self.merge(
                tmp,
                children=(("alpha", "present"), ("beta", "present")),
                repo_nodes=("alpha", "beta"),
            )
        self.assertEqual(
            graph["meta"]["cross_repo"]["resolved_children"], ["alpha", "beta"]
        )

    def test_an_unreadable_child_is_not_in_the_input_set(self) -> None:
        with TemporaryDirectory() as tmp:
            graph, _err = self.merge(
                tmp,
                children=(("alpha", "present"), ("broken", CORRUPT)),
                repo_nodes=("alpha",),
            )
        self.assertEqual(
            graph["meta"]["cross_repo"]["resolved_children"], ["alpha"]
        )

    def test_the_stamp_is_byte_identical_across_runs(self) -> None:
        stamps = []
        for _ in range(2):
            _PENDING.clear()
            _PENDING.append(_edge(f"alpha{SEP}n1", f"beta{SEP}n2"))
            with TemporaryDirectory() as tmp:
                graph, _err = self.merge(
                    tmp,
                    children=(("beta", "present"), ("alpha", "present")),
                    repo_nodes=("alpha", "beta"),
                    strategies=(_RESOLVER, _RESOLVER),
                )
            stamps.append(graph["meta"]["cross_repo"])
        self.assertEqual(stamps[0], stamps[1])
        # Duplicate strategy entries collapse; both lists stay sorted.
        self.assertEqual(stamps[0]["strategies"], [_RESOLVER])
        self.assertEqual(stamps[0]["resolved_children"], ["alpha", "beta"])


class NoResolverPassMeansNoStampTest(_MergeCase):
    """The three early returns must leave the graph as they found it."""

    def test_no_strategies(self) -> None:
        with TemporaryDirectory() as tmp:
            graph, _err = self.merge(
                tmp,
                children=(("alpha", "present"),),
                repo_nodes=("alpha",),
                strategies=(),
            )
        self.assertNotIn("cross_repo", graph["meta"])

    def test_no_present_children(self) -> None:
        root_graph = _root_graph()
        with TemporaryDirectory() as tmp:
            err = io.StringIO()
            with redirect_stderr(err):
                graph = merge_cross_repo_edges(
                    Path(tmp),
                    _config(("alpha",), strategies=(_RESOLVER,)),
                    _state(("alpha",), status="missing"),
                    root_graph,
                )
        self.assertNotIn("cross_repo", graph["meta"])

    def test_every_present_child_fails_to_load(self) -> None:
        with TemporaryDirectory() as tmp:
            graph, _err = self.merge(
                tmp, children=(("broken", CORRUPT),),
            )
        self.assertNotIn("cross_repo", graph["meta"])


if __name__ == "__main__":
    unittest.main()
