"""In-process regression CORPUS for the field-eval findings (bd uuxaz, d76r1).

Two external evaluations shipped the same synthetic 4-repo Acme workspace (an
order-schema library, a C# gateway and a Python notifier that both consume the
schema as a package dependency, and a docs repo), materialised on disk by
:func:`weld.tests._field_eval_corpus_fixture.materialize_workspace`. This file
is the in-process half of our corpus for it; the subprocess half -- the CLI the
evaluator actually drove -- is ``weld_field_eval_e2e_test`` and its sibling
regression suite.

**What the v0.24.0 evaluation taught this file.** Its first version asserted
that the ``package_graph`` resolver *emits* edges between the two consumers and
the schema repo, using node ids the test itself spelled -- and separately that
``impact`` finds dependents in a root graph the test hand-wired, in a
*different* spelling. Both halves passed. The product shipped a root graph
whose every cross-repo edge dangled, because nothing ever put the resolver's
real output into a real root meta-graph and asked whether the endpoints
existed. That is now what the two join tests do, through
:func:`weld.federation_root.build_root_meta_graph` and
:func:`weld._discover_federate.merge_cross_repo_edges`, checked with
:func:`weld.tests._graph_invariants.assert_edges_resolve`.

The assertions that reproduce an *unfixed* v0.24.0 finding carry its id and bd
issue and are expected failures until that fix lands -- the same contract as
the E2E probes, so the corpus is never quietly weakened to stay green.

What each remaining class pins:

* **Finding 06 (cross-repo join).** Both consumers join to the producing schema
  repo -- the C# ``<PackageReference>`` and the Python ``pyproject`` dependency
  -- and the resolver's output is byte-stable.
* **Finding 06 (impact cannot-answer).** ``impact`` on the schema ``repo:``
  node over the *default* ``cross_repo_strategies: []`` graph reports
  ``Risk: UNKNOWN`` / ``result_unknown``, not a fabricated ``LOW / 0``.
* **Finding 06 (loop closed).** Wiring edges into the meta graph turns that
  UNKNOWN into a measured answer.
* **Finding 05 (unclaimed source).** The gateway child, whose ``discover.yaml``
  predates the C# strategy, surfaces ``csharp`` as unclaimed.
* **Finding 02 (no-graph precondition).** A graph-less federation root refuses
  a graph-backed read, distinct from an answered-empty result.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from weld._discover_federate import merge_cross_repo_edges
from weld._errors import RESULT_UNKNOWN
from weld._graph_cli import main as graph_cli_main
from weld._unclaimed_sources import detect_unclaimed_source_classes
from weld.contract import SCHEMA_VERSION
from weld.federation_root import build_root_meta_graph
from weld.graph import Graph
from weld.impact_core import format_human, impact
from weld.tests._field_eval_corpus_fixture import (
    BILLING,
    CHILDREN,
    GATEWAY,
    NOTIFY,
    SCHEMA,
    materialize_workspace,
)
from weld.tests._field_eval_corpus_sources_csharp import BILLING_PACKAGE_ID
from weld.tests._field_eval_e2e_harness import cross_repo_joins
from weld.tests._graph_invariants import (
    assert_answered_empty,
    assert_cannot_answer,
    assert_edges_resolve,
    graph_edges,
)
from weld.workspace_state import build_workspace_state, load_workspace_config

_TS = "2026-08-29T00:00:00+00:00"

#: The joins the manifests on disk genuinely support. Everything else the
#: resolver emits comes from the vendored ``.venv`` (finding N2). The third
#: arrived with the corpus's C#-only producer child, whose package name is
#: declared only in an MSBuild ``<PackageId>`` (finding M4, bd lcq0c.4).
_REAL_JOINS = {
    (GATEWAY[0], SCHEMA[0], "Acme.Platform.Order.Schema"),
    (GATEWAY[0], BILLING[0], BILLING_PACKAGE_ID),
    (NOTIFY[0], SCHEMA[0], "order-schema"),
}


def _graph(nodes: dict, edges: list[dict] | None = None) -> Graph:
    graph = Graph(Path("."))
    graph._data = {
        "meta": {
            "version": SCHEMA_VERSION,
            "updated_at": _TS,
            "schema_version": 2,
        },
        "nodes": nodes,
        "edges": edges or [],
    }
    return graph


def _repo_node(name: str) -> dict:
    return {"type": "repo", "label": name, "props": {"path": name}}


class _FederatedRootMixin:
    """Builds the root meta-graph the way ``wd discover`` builds it.

    No hand-written ``ResolverContext`` and no hand-spelled edge ids: the
    workspace config comes off disk, the ledger is the real one, and the edges
    are whatever the resolver produced for that context. Anything less is how
    two halves of this file came to use different id conventions without either
    noticing.
    """

    def _federate(self, root: Path) -> tuple[dict, dict[str, dict]]:
        config = load_workspace_config(root)
        assert config is not None, "fixture wrote no workspaces.yaml"
        state = build_workspace_state(root, config)
        meta = build_root_meta_graph(root, config, state, now=_TS)
        merged = merge_cross_repo_edges(root, config, state, meta)
        children = {
            name: json.loads(
                (root / rel / ".weld" / "graph.json").read_text(encoding="utf-8")
            )
            for name, rel in CHILDREN
        }
        return merged, children

    def _workspace(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)  # type: ignore[attr-defined]
        return materialize_workspace(
            Path(tmp.name) / "ws",
            git=True,
            preseed=True,
            cross_repo_strategies=("package_graph",),
        )


class CrossRepoManifestJoinTest(_FederatedRootMixin, unittest.TestCase):
    """Finding 06: both consumers join to the producing schema repo."""

    def test_csharp_and_python_consumers_both_join_the_schema_repo(self) -> None:
        merged, _children = self._federate(self._workspace())
        self.assertTrue(
            _REAL_JOINS <= cross_repo_joins(merged),
            f"a real manifest join is missing: {sorted(cross_repo_joins(merged))}",
        )

    def test_resolver_output_is_byte_stable(self) -> None:
        root = self._workspace()
        config = load_workspace_config(root)
        state = build_workspace_state(root, config)
        first = merge_cross_repo_edges(
            root, config, state, build_root_meta_graph(root, config, state, now=_TS)
        )
        second = merge_cross_repo_edges(
            root, config, state, build_root_meta_graph(root, config, state, now=_TS)
        )
        self.assertEqual(graph_edges(first), graph_edges(second))

    def test_every_merged_edge_resolves_in_the_root_graph(self) -> None:
        # Holds since ADR 0137 ss4: an edge whose endpoints resolve to nothing
        # is dropped rather than written. Today that is why the test above is
        # red -- package_graph's own edges are the ones being dropped -- so
        # this passes over an empty edge set. It stops being vacuous when N1
        # (d76r1.4) fixes the endpoint spelling, and it is a standing gate
        # either way: nothing unresolvable may reach the root graph.
        merged, children = self._federate(self._workspace())
        assert_edges_resolve(merged, children)

    def test_the_vendored_tree_contributes_no_join(self) -> None:
        merged, _children = self._federate(self._workspace())
        self.assertEqual(cross_repo_joins(merged), _REAL_JOINS)


class ImpactCannotAnswerAtRootTest(unittest.TestCase):
    """Finding 06: the default cross_repo_strategies:[] root cannot answer."""

    def test_schema_repo_impact_is_unknown_not_fabricated_low(self) -> None:
        # The default federation root carries only repo nodes and no cross-repo
        # edges (cross_repo_strategies: []), exactly the shipped default.
        graph = _graph({f"repo:{SCHEMA[0]}": _repo_node(SCHEMA[0])})

        result = impact(graph, target=f"repo:{SCHEMA[0]}")

        self.assertEqual(result["risk_level"], "UNKNOWN")
        marker = result.get("cannot_answer")
        self.assertIsInstance(marker, dict)
        self.assertEqual(marker["error_code"], RESULT_UNKNOWN)
        self.assertIn("cross_repo_strategies", marker["reason"])
        self.assertEqual(result["direct_dependents"], [])
        rendered = format_human(result)
        self.assertIn("Risk: UNKNOWN", rendered)
        self.assertNotIn("Risk: LOW", rendered)


class ImpactAnsweredAfterWiringTest(_FederatedRootMixin, unittest.TestCase):
    """Finding 06 loop closed: cross-repo edges make impact answerable.

    Two tests, deliberately: the control wires edges by hand and proves the
    traversal half works at all, so a failure in the real-path test below is
    attributable to what the resolver wrote rather than to ``impact``. Before
    v0.24.0 only the control existed, and it passed while the product's own
    root graph could not answer the same question.
    """

    def test_hand_wired_edges_yield_measured_dependents(self) -> None:
        nodes = {
            f"repo:{name}": _repo_node(name)
            for name in (SCHEMA[0], GATEWAY[0], NOTIFY[0])
        }
        edges = [
            {
                "from": f"repo:{consumer}",
                "to": f"repo:{SCHEMA[0]}",
                "type": "cross_repo:depends_on",
                "props": {},
            }
            for consumer in (GATEWAY[0], NOTIFY[0])
        ]

        result = impact(_graph(nodes, edges), target=f"repo:{SCHEMA[0]}")

        self.assertNotEqual(result["risk_level"], "UNKNOWN")
        self.assertNotIn("cannot_answer", result)
        direct = {d["id"] for d in result["direct_dependents"]}
        self.assertIn(f"repo:{GATEWAY[0]}", direct)
        self.assertIn(f"repo:{NOTIFY[0]}", direct)

    def test_the_resolvers_own_edges_yield_measured_dependents(self) -> None:
        merged, _children = self._federate(self._workspace())

        result = impact(_graph(merged["nodes"], graph_edges(merged)),
                        target=f"repo:{SCHEMA[0]}")

        self.assertNotEqual(result["risk_level"], "UNKNOWN", result.get("cannot_answer"))
        self.assertNotIn("cannot_answer", result)
        direct = {d["id"] for d in result["direct_dependents"]}
        self.assertIn(f"repo:{GATEWAY[0]}", direct)
        self.assertIn(f"repo:{NOTIFY[0]}", direct)


class DoctorUnclaimedSourceTest(unittest.TestCase):
    """Finding 05: a markdown-only config leaves the C# source unclaimed."""

    def test_gateway_csharp_is_unclaimed_under_markdown_only_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = materialize_workspace(Path(tmp) / "ws", preseed=True)
            unclaimed = detect_unclaimed_source_classes(root / GATEWAY[1])

        languages = {c.language for c in unclaimed}
        self.assertIn("csharp", languages)

    def test_python_notifier_config_claims_its_source(self) -> None:
        # Contrast child: the notifier's config wires python_module, so its
        # source is claimed and nothing is flagged. Pins that the check reports
        # a gap only where one genuinely exists.
        with tempfile.TemporaryDirectory() as tmp:
            root = materialize_workspace(Path(tmp) / "ws", preseed=True)
            unclaimed = detect_unclaimed_source_classes(root / NOTIFY[1])
        self.assertEqual(
            [c.language for c in unclaimed if c.language == "python"], []
        )


class NoGraphPreconditionTest(unittest.TestCase):
    """Finding 02: a graph-less federation root cannot answer graph reads."""

    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        code = 0
        try:
            with redirect_stdout(out), redirect_stderr(err):
                graph_cli_main(argv)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        return code, out.getvalue(), err.getvalue()

    def test_graphless_root_query_refuses_with_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = materialize_workspace(Path(tmp) / "ws")
            # No root .weld/graph.json -> the fresh-worktree graph-less state.
            code, out, err = self._run(["--root", str(root), "query", "OrderReplayer"])

        assert_cannot_answer(code, err, out)
        self.assertIn("No Weld graph found.", err)

    def test_present_empty_root_graph_answers_empty_not_cannot_answer(self) -> None:
        # The other side of the same contract, and the reason both helpers
        # exist: a check that only pins the refusal passes on a tool that has
        # started refusing questions it can answer.
        with tempfile.TemporaryDirectory() as tmp:
            root = materialize_workspace(Path(tmp) / "ws")
            payload = {"meta": {"version": 1}, "nodes": {}, "edges": []}
            (root / ".weld" / "graph.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
            code, out, err = self._run(["--root", str(root), "query", "OrderReplayer"])

        assert_answered_empty(code, out, err)


if __name__ == "__main__":
    unittest.main()
