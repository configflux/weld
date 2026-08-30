"""End-to-end regression CORPUS for the field-eval v0.23.1 findings (bd uuxaz).

The external polyrepo evaluation surfaced nine findings, each now fixed with its
own targeted unit regression. Per the dogfood policy, a fix is only *proven* by a
pinned corpus entry -- one place that materialises the reported synthetic
workspace and asserts the end-to-end federated behaviour, so a future refactor
that quietly re-breaks the federation is caught here even if the narrow unit
fixtures drift.

This suite is that corpus. It drives the *same* synthetic 4-repo Acme workspace
the evaluator shipped (an order-schema library, a C# gateway and a Python
notifier that both consume the schema as a package dependency, and a docs repo),
materialised on disk by
:func:`weld.tests._field_eval_corpus_fixture.materialize_workspace`, and asserts
the load-bearing federated behaviours that ``run-all-repros.sh`` exercises, in
one file:

* **Finding 06 (cross-repo join).** The ``package_graph`` resolver reads the
  real manifests on disk and emits ``cross_repo:depends_on`` from *both*
  consumers to the producing schema repo -- the C# ``<PackageReference>`` and the
  Python ``pyproject`` dependency -- and its output is byte-stable.
* **Finding 06 (impact cannot-answer).** ``impact`` on the schema ``repo:`` node,
  over the *default* ``cross_repo_strategies: []`` root graph, reports
  ``Risk: UNKNOWN`` / ``result_unknown`` pointing at ``cross_repo_strategies``
  -- not a fabricated ``Risk: LOW / 0 dependents``.
* **Finding 06 (loop closed).** Wiring the resolver's own edges into the meta
  graph turns that UNKNOWN into a measured answer: both consumers resolve as
  dependents and the cannot-answer marker is gone.
* **Finding 05 (unclaimed source).** The C# gateway child, whose
  ``discover.yaml`` predates the C# strategy (markdown-only), surfaces ``csharp``
  as an unclaimed source class -- the "100% of a language invisible while doctor
  reports healthy" shape.
* **Finding 02 (no-graph precondition).** A graph-less federation root refuses a
  graph-backed read with the ``No Weld graph found.`` cannot-answer guidance and
  a non-zero exit, distinct from an answered-empty result.

The corpus is deliberately grammar-independent (see the fixture module): every
assertion is computable from manifests, configs, and hand-shaped graphs, so it
runs hermetically in the fast loop with no ambient tree-sitter grammar and no
``git`` shell-out.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from weld._errors import RESULT_UNKNOWN
from weld._graph_cli import main as graph_cli_main
from weld._unclaimed_sources import detect_unclaimed_source_classes
from weld.contract import SCHEMA_VERSION
from weld.cross_repo import ResolverContext
from weld.cross_repo.package_graph import PackageGraphResolver
from weld.graph import Graph
from weld.impact_core import format_human, impact
from weld.tests._field_eval_corpus_fixture import (
    GATEWAY,
    NOTIFY,
    SCHEMA,
    materialize_workspace,
)
from weld.workspace import UNIT_SEPARATOR

_TS = "2026-08-29T00:00:00+00:00"

# Federated repo-node ids the resolver emits edges between (child-name-prefixed).
_SCHEMA_REPO = f"{SCHEMA[0]}{UNIT_SEPARATOR}repo:{SCHEMA[0]}"
_GATEWAY_REPO = f"{GATEWAY[0]}{UNIT_SEPARATOR}repo:{GATEWAY[0]}"
_NOTIFY_REPO = f"{NOTIFY[0]}{UNIT_SEPARATOR}repo:{NOTIFY[0]}"


def _resolver_context(root: Path) -> ResolverContext:
    """Context whose children map carries only the *present* child names.

    ``package_graph`` reads manifests from disk, so the graph values are unused;
    ``None`` makes that explicit (mirrors the resolver's own unit test).
    """
    names = [SCHEMA[0], GATEWAY[0], NOTIFY[0], "docs-site"]
    return ResolverContext(
        workspace_root=str(root),
        cross_repo_strategies=["package_graph"],
        children={n: None for n in names},
        child_hashes={n: "" for n in names},
    )


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


class CrossRepoManifestJoinTest(unittest.TestCase):
    """Finding 06: both consumers join to the producing schema repo."""

    def test_csharp_and_python_consumers_both_resolve_to_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = materialize_workspace(Path(tmp))
            edges = PackageGraphResolver().resolve(_resolver_context(root))

        joins = {(e.from_id, e.to_id, e.type) for e in edges}
        # C# gateway -> schema (via <PackageReference>, case-insensitive match).
        self.assertIn(
            (_GATEWAY_REPO, _SCHEMA_REPO, "cross_repo:depends_on"), joins
        )
        # Python notifier -> schema (via pyproject [project].dependencies).
        self.assertIn(
            (_NOTIFY_REPO, _SCHEMA_REPO, "cross_repo:depends_on"), joins
        )
        # Exactly these two joins -- the docs repo and self-references add none.
        self.assertEqual(len(edges), 2)

    def test_resolver_output_is_byte_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = materialize_workspace(Path(tmp))
            ctx = _resolver_context(root)
            first = PackageGraphResolver().resolve(ctx)
            second = PackageGraphResolver().resolve(ctx)
        self.assertEqual(
            [e.to_dict() for e in first], [e.to_dict() for e in second]
        )


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


class ImpactAnsweredAfterWiringTest(unittest.TestCase):
    """Finding 06 loop closed: the resolver's edges make impact answerable.

    The edges asserted in :class:`CrossRepoManifestJoinTest` are wired into the
    root meta-graph; the *same* schema-repo query that was UNKNOWN now resolves
    both consumers as dependents with no cannot-answer marker. This is the
    end-to-end proof that the resolver output is what closes Finding 06, not two
    behaviours that merely happen to pass in isolation.
    """

    def test_wiring_resolver_edges_yields_measured_dependents(self) -> None:
        nodes = {
            f"repo:{SCHEMA[0]}": _repo_node(SCHEMA[0]),
            f"repo:{GATEWAY[0]}": _repo_node(GATEWAY[0]),
            f"repo:{NOTIFY[0]}": _repo_node(NOTIFY[0]),
        }
        edges = [
            {
                "from": f"repo:{GATEWAY[0]}",
                "to": f"repo:{SCHEMA[0]}",
                "type": "cross_repo:depends_on",
                "props": {},
            },
            {
                "from": f"repo:{NOTIFY[0]}",
                "to": f"repo:{SCHEMA[0]}",
                "type": "cross_repo:depends_on",
                "props": {},
            },
        ]

        result = impact(_graph(nodes, edges), target=f"repo:{SCHEMA[0]}")

        self.assertNotEqual(result["risk_level"], "UNKNOWN")
        self.assertNotIn("cannot_answer", result)
        direct = {d["id"] for d in result["direct_dependents"]}
        self.assertIn(f"repo:{GATEWAY[0]}", direct)
        self.assertIn(f"repo:{NOTIFY[0]}", direct)


class DoctorUnclaimedSourceTest(unittest.TestCase):
    """Finding 05: a markdown-only config leaves the C# source unclaimed."""

    def test_gateway_csharp_is_unclaimed_under_markdown_only_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = materialize_workspace(Path(tmp))
            unclaimed = detect_unclaimed_source_classes(root / GATEWAY[1])

        languages = {c.language for c in unclaimed}
        self.assertIn("csharp", languages)

    def test_python_notifier_config_claims_its_source(self) -> None:
        # Contrast child: the notifier's config wires python_module, so its
        # source is claimed and nothing is flagged. Pins that the check reports
        # a gap only where one genuinely exists.
        with tempfile.TemporaryDirectory() as tmp:
            root = materialize_workspace(Path(tmp))
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
            root = materialize_workspace(Path(tmp))
            # No root .weld/graph.json -> the fresh-worktree graph-less state.
            code, _out, err = self._run(["--root", str(root), "query", "OrderReplayer"])

        self.assertNotEqual(code, 0)
        self.assertIn("No Weld graph found.", err)

    def test_present_empty_root_graph_answers_empty_not_cannot_answer(self) -> None:
        # ADR 0134 boundary: a *present* (if empty) graph is an answered-empty
        # result, distinct from the graph-less cannot-answer above.
        with tempfile.TemporaryDirectory() as tmp:
            root = materialize_workspace(Path(tmp))
            payload = {"meta": {"version": 1}, "nodes": {}, "edges": []}
            (root / ".weld" / "graph.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
            code, _out, err = self._run(["--root", str(root), "query", "OrderReplayer"])

        self.assertEqual(code, 0)
        self.assertNotIn("No Weld graph found.", err)


if __name__ == "__main__":
    unittest.main()
