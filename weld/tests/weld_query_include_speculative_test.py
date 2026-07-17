"""End-to-end CLI tests for default speculative-match filtering.

Trust fix (epic agent-trust quick wins): ``wd query`` text output is the
primary agent surface, yet ~35% of a typical result set is
``origin=unresolved`` call-graph sentinels the agent cannot discount,
because the text renderer also stripped ``props.confidence``. This module
drives the CLI ``main`` dispatcher end-to-end to pin the acceptance
criteria:

1. default text output contains no ``origin=unresolved`` matches;
2. ``confidence`` is shown per match in text output;
3. ``--include-speculative`` restores the unfiltered result set;
4. the same filter applies to ``--json`` (the CLI envelope), while the
   MCP / direct ``Graph.query`` path is verified elsewhere to stay full;
5. the default ``--json`` envelope is self-consistent -- ``neighbors`` and
   ``edges`` are re-derived for the surviving matches, so a strict consumer
   never sees an edge endpoint or neighbour that is absent from
   ``matches``+``neighbors`` (covered both as a unit test of
   ``trim_envelope_to_matches`` and end-to-end over an edge-bearing fixture),
   and ``--include-speculative`` leaves the envelope equal to the raw
   ``Graph.query`` result.

These live in a dedicated module so ``weld_cli_default_human_format_test``
and the ranking tests stay under the 400-line cap.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


from weld._graph_cli import main as cli_main  # noqa: E402


def _write_graph(root: Path) -> None:
    """Materialize a graph with a definite match and an unresolved sentinel.

    Both nodes share the ``summary`` token so they co-occur in the same
    query result; ``resolution_penalty`` already ranks the definite node
    first, and the default CLI filter must additionally drop the sentinel.
    """
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    nodes = {
        "symbol:py:weld.report:build_summary": {
            "type": "symbol",
            "label": "build_summary",
            "props": {
                "origin": "project",
                "authority": "derived",
                "confidence": "definite",
                "file": "weld/report.py",
            },
        },
        "symbol:unresolved:summary": {
            "type": "symbol",
            "label": "summary",
            "props": {
                "origin": "unresolved",
                "authority": "derived",
                "confidence": "speculative",
                "resolution": "unresolved",
            },
        },
    }
    payload = {
        "meta": {"version": 1, "schema_version": 1},
        "nodes": nodes,
        "edges": [],
    }
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self.root = Path(self._tmp)
        _write_graph(self.root)
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        import shutil

        shutil.rmtree(self._tmp, ignore_errors=True)

    def _run(self, *args: str) -> str:
        """Drive the CLI; ``--root`` is a top-level flag so it precedes the
        subcommand, while ``--no-refresh`` is a query-subparser flag."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_main(["--root", str(self.root), *args, "--no-refresh"])
        return buf.getvalue()


class DefaultFilterTextTest(_Base):
    """Criteria 1 + 2: default text hides unresolved, shows confidence."""

    def test_default_text_omits_unresolved_sentinel(self) -> None:
        out = self._run("query", "summary")
        self.assertIn("symbol:py:weld.report:build_summary", out)
        self.assertNotIn("symbol:unresolved:summary", out)

    def test_default_text_shows_confidence(self) -> None:
        out = self._run("query", "summary")
        self.assertIn("confidence: definite", out)


class IncludeSpeculativeTest(_Base):
    """Criterion 3: ``--include-speculative`` restores the sentinel."""

    def test_flag_restores_unresolved_sentinel_text(self) -> None:
        out = self._run("query", "summary", "--include-speculative")
        self.assertIn("symbol:unresolved:summary", out)
        self.assertIn("symbol:py:weld.report:build_summary", out)

    def test_flag_restores_unresolved_sentinel_json(self) -> None:
        out = self._run("query", "summary", "--include-speculative", "--json")
        ids = {m["id"] for m in json.loads(out)["matches"]}
        self.assertIn("symbol:unresolved:summary", ids)
        self.assertIn("symbol:py:weld.report:build_summary", ids)


class DefaultFilterJsonTest(_Base):
    """Criterion 4: the CLI ``--json`` envelope is filtered by default too."""

    def test_default_json_omits_unresolved_sentinel(self) -> None:
        out = self._run("query", "summary", "--json")
        ids = {m["id"] for m in json.loads(out)["matches"]}
        self.assertIn("symbol:py:weld.report:build_summary", ids)
        self.assertNotIn("symbol:unresolved:summary", ids)


class CoreQueryStaysFullTest(_Base):
    """The core ``Graph.query`` (MCP / API path) is NOT filtered."""

    def test_graph_query_still_returns_sentinel(self) -> None:
        from weld.graph import Graph

        g = Graph(self.root)
        g.load()
        ids = {m["id"] for m in g.query("summary")["matches"]}
        self.assertIn("symbol:unresolved:summary", ids)
        self.assertIn("symbol:py:weld.report:build_summary", ids)


class TrimEnvelopeUnitTest(unittest.TestCase):
    """Unit contract for :func:`weld._query_envelope.trim_envelope_to_matches`."""

    def _envelope(self) -> dict:
        """Pre-filter envelope: surviving match ``M`` + dropped sentinel ``S``.

        Edges: ``M->N`` (legit neighbour of a survivor), ``S->O`` (orphan
        reachable only via the dropped sentinel), and ``M->S`` (a survivor
        pointing at the dropped match, which must demote ``S`` to a neighbour
        carrying its full node). ``compute_neighborhood`` puts ``S`` in
        ``matches`` (not ``neighbors``) pre-filter, so its node dict is only
        available from the envelope's ``matches`` list. The helper receives the
        *pre-filter* envelope plus the surviving id set ``{"M"}``.
        """
        return {
            "query": "x",
            "matches": [
                {"id": "M", "type": "symbol", "props": {"origin": "project"}},
                {"id": "S", "type": "symbol", "props": {"origin": "unresolved"}},
            ],
            "neighbors": [
                {"id": "N", "type": "symbol", "props": {"origin": "project"}},
                {"id": "O", "type": "symbol", "props": {"origin": "project"}},
            ],
            "edges": [
                {"from": "M", "to": "N", "type": "calls"},
                {"from": "S", "to": "O", "type": "calls"},
                {"from": "M", "to": "S", "type": "calls"},
            ],
        }

    def test_drops_dangling_edges_and_orphan_neighbors(self) -> None:
        from weld._query_envelope import trim_envelope_to_matches

        out = trim_envelope_to_matches(self._envelope(), {"M"})
        edge_pairs = {(e["from"], e["to"]) for e in out["edges"]}
        # S->O has no surviving endpoint -> dropped; O orphaned -> gone.
        self.assertNotIn(("S", "O"), edge_pairs)
        self.assertNotIn("O", {n["id"] for n in out["neighbors"]})
        # M->N survives; N retained as a legit neighbour of survivor M.
        self.assertIn(("M", "N"), edge_pairs)
        self.assertIn("N", {n["id"] for n in out["neighbors"]})

    def test_former_match_demoted_to_neighbor_keeps_node(self) -> None:
        from weld._query_envelope import trim_envelope_to_matches

        out = trim_envelope_to_matches(self._envelope(), {"M"})
        # M->S survives (M is a survivor); S becomes a neighbour with its node,
        # NOT a dangling edge endpoint -- the regression this guards against.
        self.assertIn(("M", "S"), {(e["from"], e["to"]) for e in out["edges"]})
        by_id = {n["id"]: n for n in out["neighbors"]}
        self.assertIn("S", by_id)
        self.assertEqual(by_id["S"]["props"]["origin"], "unresolved")
        # S is no longer a *match* (it was dropped from the ranked result).
        self.assertNotIn("S", {m["id"] for m in out["matches"]})

    def test_matches_filtered_and_envelope_self_consistent(self) -> None:
        from weld._query_envelope import trim_envelope_to_matches

        out = trim_envelope_to_matches(self._envelope(), {"M"})
        # Helper filters matches to the surviving id set (order preserved).
        self.assertEqual([m["id"] for m in out["matches"]], ["M"])
        # Neighbours are sorted, and the whole envelope satisfies the
        # strict-reconstruction invariant.
        self.assertEqual(
            [n["id"] for n in out["neighbors"]],
            sorted(n["id"] for n in out["neighbors"]),
        )
        _assert_json_self_consistent(self, out)


def _assert_json_self_consistent(test: unittest.TestCase, env: dict) -> None:
    """Assert the strict-reconstruction invariant on a query ``--json`` env.

    Every edge endpoint must resolve to a node present in ``matches`` or
    ``neighbors``; every neighbour must be an endpoint of some edge and must
    not also be a match (matches and neighbours are disjoint).
    """
    match_ids = {m["id"] for m in env["matches"]}
    neighbor_ids = {n["id"] for n in env["neighbors"]}
    known = match_ids | neighbor_ids
    edge_endpoints: set[str] = set()
    for e in env["edges"]:
        edge_endpoints.add(e["from"])
        edge_endpoints.add(e["to"])
        test.assertIn(e["from"], known, f"edge.from {e['from']} not in matches/neighbors")
        test.assertIn(e["to"], known, f"edge.to {e['to']} not in matches/neighbors")
    test.assertEqual(match_ids & neighbor_ids, set(), "matches/neighbors overlap")
    for nid in neighbor_ids:
        test.assertIn(nid, edge_endpoints, f"neighbor {nid} has no incident edge")


def _write_edge_graph(root: Path) -> None:
    """Graph with a resolved match, an unresolved sentinel match, and edges.

    Both ``M`` (resolved ``make_widget``) and ``S`` (unresolved sentinel
    ``widget``) match the token ``widget``. ``N`` is a legit 1-hop neighbour of
    survivor ``M``; ``O`` is reachable only from the dropped sentinel ``S``.
    Neither ``N`` nor ``O`` matches ``widget`` (so they only ever appear as
    neighbours). Edges cover all three trim cases:

    * ``M->N``  -- legit context of a survivor, must be kept;
    * ``S->O``  -- dangling once ``S`` is dropped, must be removed (``O`` too);
    * ``M->S``  -- survivor pointing at the dropped sentinel: the edge is kept
      and ``S`` is demoted from match to neighbour (must NOT dangle).
    """
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    nodes = {
        "symbol:py:pkg.mod:make_widget": {
            "type": "symbol", "label": "make_widget",
            "props": {"origin": "project", "confidence": "definite",
                      "file": "pkg/mod.py"},
        },
        "symbol:unresolved:widget": {
            "type": "symbol", "label": "widget",
            "props": {"origin": "unresolved", "confidence": "speculative",
                      "resolution": "unresolved"},
        },
        "symbol:py:pkg.mod:neighbor_of_survivor": {
            "type": "symbol", "label": "neighbor_of_survivor",
            "props": {"origin": "project", "confidence": "definite",
                      "file": "pkg/mod.py"},
        },
        "symbol:py:pkg.mod:orphan_of_sentinel": {
            "type": "symbol", "label": "orphan_of_sentinel",
            "props": {"origin": "project", "confidence": "definite",
                      "file": "pkg/mod.py"},
        },
    }
    edges = [
        {"from": "symbol:py:pkg.mod:make_widget",
         "to": "symbol:py:pkg.mod:neighbor_of_survivor", "type": "calls"},
        {"from": "symbol:unresolved:widget",
         "to": "symbol:py:pkg.mod:orphan_of_sentinel", "type": "calls"},
        {"from": "symbol:py:pkg.mod:make_widget",
         "to": "symbol:unresolved:widget", "type": "calls"},
    ]
    payload = {"meta": {"version": 1, "schema_version": 1},
               "nodes": nodes, "edges": edges}
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )


class _EdgeBase(_Base):
    """Like :class:`_Base` but materializes the edge-bearing fixture."""

    def setUp(self) -> None:
        super().setUp()
        _write_edge_graph(self.root)


class DefaultJsonEnvelopeSelfConsistentTest(_EdgeBase):
    """AC1/AC2: default ``--json`` trims neighbors/edges to surviving matches."""

    def test_sentinel_dropped_from_matches(self) -> None:
        env = json.loads(self._run("query", "widget", "--json"))
        ids_seen = {m["id"] for m in env["matches"]}
        self.assertIn("symbol:py:pkg.mod:make_widget", ids_seen)
        self.assertNotIn("symbol:unresolved:widget", ids_seen)

    def test_orphan_of_dropped_sentinel_removed(self) -> None:
        env = json.loads(self._run("query", "widget", "--json"))
        all_ids = {n["id"] for n in env["neighbors"]}
        all_ids.update(e["from"] for e in env["edges"])
        all_ids.update(e["to"] for e in env["edges"])
        # ``O`` was reachable ONLY via the dropped sentinel (S->O); the edge
        # and the orphan must both be gone.
        self.assertNotIn("symbol:py:pkg.mod:orphan_of_sentinel", all_ids)
        self.assertNotIn(
            ("symbol:unresolved:widget", "symbol:py:pkg.mod:orphan_of_sentinel"),
            {(e["from"], e["to"]) for e in env["edges"]},
        )

    # The trim demotes the dropped sentinel ``widget`` to a neighbour (via the
    # survivor edge ``make_widget -> widget``); the neighbor diet then drops it
    # (origin=unresolved) and its now-dangling edge, annotating the loss.
    # ``--full-neighborhood`` turns the diet off and restores both.
    _SENTINEL_EDGE = ("symbol:py:pkg.mod:make_widget", "symbol:unresolved:widget")

    def test_dropped_sentinel_neighbor_removed_by_diet_default(self) -> None:
        env = json.loads(self._run("query", "widget", "--json"))
        self.assertNotIn("symbol:unresolved:widget", {n["id"] for n in env["neighbors"]})
        self.assertNotIn(self._SENTINEL_EDGE, {(e["from"], e["to"]) for e in env["edges"]})
        self.assertTrue(env["neighbors_filtered"])
        self.assertGreaterEqual(env["omitted_neighbors"]["unresolved"], 1)

    def test_full_neighborhood_restores_demoted_sentinel(self) -> None:
        env = json.loads(self._run("query", "widget", "--json", "--full-neighborhood"))
        self.assertIn(self._SENTINEL_EDGE, {(e["from"], e["to"]) for e in env["edges"]})
        self.assertIn("symbol:unresolved:widget", {n["id"] for n in env["neighbors"]})
        self.assertNotIn("neighbors_filtered", env)

    def test_legit_neighbor_of_survivor_retained(self) -> None:
        env = json.loads(self._run("query", "widget", "--json"))
        self.assertIn(
            "symbol:py:pkg.mod:neighbor_of_survivor",
            {n["id"] for n in env["neighbors"]},
        )

    def test_envelope_is_self_consistent(self) -> None:
        env = json.loads(self._run("query", "widget", "--json"))
        _assert_json_self_consistent(self, env)


class IncludeSpeculativeJsonNonRegressionTest(_EdgeBase):
    """AC3: ``--include-speculative --json`` equals the raw, untrimmed env."""

    def test_flag_restores_sentinel_orphan_and_edges(self) -> None:
        from weld.graph import Graph

        env = json.loads(
            self._run("query", "widget", "--include-speculative", "--json")
        )
        g = Graph(self.root)
        g.load()
        raw = g.query("widget")
        self.assertEqual(
            {m["id"] for m in env["matches"]},
            {m["id"] for m in raw["matches"]},
        )
        self.assertEqual(
            {n["id"] for n in env["neighbors"]},
            {n["id"] for n in raw["neighbors"]},
        )
        self.assertEqual(
            {(e["from"], e["to"]) for e in env["edges"]},
            {(e["from"], e["to"]) for e in raw["edges"]},
        )
        # Sanity: the sentinel + its orphan edge are present under the flag.
        self.assertIn("symbol:unresolved:widget", {m["id"] for m in env["matches"]})
        self.assertIn(
            ("symbol:unresolved:widget", "symbol:py:pkg.mod:orphan_of_sentinel"),
            {(e["from"], e["to"]) for e in env["edges"]},
        )


if __name__ == "__main__":
    unittest.main()
