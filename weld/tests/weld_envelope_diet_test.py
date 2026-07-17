"""Tests for the query/context envelope neighbor diet (bd d1oc).

Layered coverage for :mod:`weld._envelope_diet` and its two surface wirings:

* unit -- the pure ``diet_envelope`` / ``neighbor_exclude_reason`` contract:
  origin filter, external-package retention, edge de-dangling, fan-out cap,
  omission annotation, escape hatch, error-payload pass-through, determinism;
* integration (CLI) -- ``wd query`` / ``wd context`` end-to-end over a fixture
  graph with stdlib/unresolved hub spray: default is bounded + annotated,
  ``--full-neighborhood`` restores the raw neighborhood;
* integration (MCP) -- ``weld_query`` / ``weld_context`` dispatch apply the
  same diet and honour the ``full_neighborhood`` parameter.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from weld._envelope_diet import (
    DEFAULT_NEIGHBOR_CAP,
    OMISSION_REASONS,
    diet_envelope,
    neighbor_exclude_reason,
)
from weld._graph_cli import main as cli_main
from weld.mcp_server import dispatch as mcp_dispatch


def _neighbor(node_id: str, origin: str, ntype: str = "symbol") -> dict:
    return {"id": node_id, "type": ntype, "props": {"origin": origin}}


class ExcludeReasonTest(unittest.TestCase):
    """Unit contract for :func:`neighbor_exclude_reason`."""

    def test_stdlib_origin_excluded(self) -> None:
        self.assertEqual(
            neighbor_exclude_reason(_neighbor("symbol:py:os:getcwd", "stdlib")),
            "stdlib",
        )

    def test_unresolved_origin_excluded(self) -> None:
        self.assertEqual(
            neighbor_exclude_reason(_neighbor("symbol:py:m:f", "unresolved")),
            "unresolved",
        )

    def test_unresolved_id_prefix_excluded_regardless_of_origin(self) -> None:
        # A ``symbol:unresolved:`` sentinel that carries origin=stdlib (a
        # builtin such as ``int``) is still an unresolved sentinel.
        self.assertEqual(
            neighbor_exclude_reason(_neighbor("symbol:unresolved:int", "stdlib")),
            "unresolved",
        )

    def test_external_symbol_excluded(self) -> None:
        self.assertEqual(
            neighbor_exclude_reason(
                _neighbor("symbol:py:numpy:array", "external", "symbol")
            ),
            "external_symbol",
        )

    def test_external_package_kept(self) -> None:
        self.assertIsNone(
            neighbor_exclude_reason(
                _neighbor("package:python:numpy", "external", "package")
            )
        )

    def test_project_kept(self) -> None:
        self.assertIsNone(
            neighbor_exclude_reason(_neighbor("symbol:py:pkg:f", "project"))
        )

    def test_origin_less_kept(self) -> None:
        self.assertIsNone(neighbor_exclude_reason({"id": "doc:readme", "type": "doc"}))


class DietFilterTest(unittest.TestCase):
    """Unit: origin filter, edge de-dangling, annotation shape."""

    def _envelope(self) -> dict:
        return {
            "query": "x",
            "matches": [_neighbor("M", "project")],
            "neighbors": [
                _neighbor("N", "project"),
                _neighbor("PKG", "external", "package"),
                _neighbor("EXSYM", "external", "symbol"),
                _neighbor("STD", "stdlib"),
                _neighbor("symbol:unresolved:z", "unresolved"),
            ],
            "edges": [
                {"from": "M", "to": "N", "type": "calls"},
                {"from": "M", "to": "PKG", "type": "imports"},
                {"from": "M", "to": "EXSYM", "type": "calls"},
                {"from": "M", "to": "STD", "type": "calls"},
                {"from": "M", "to": "symbol:unresolved:z", "type": "calls"},
            ],
        }

    def test_keeps_project_and_external_package_only(self) -> None:
        out = diet_envelope(self._envelope())
        self.assertEqual({n["id"] for n in out["neighbors"]}, {"N", "PKG"})

    def test_drops_dangling_edges(self) -> None:
        out = diet_envelope(self._envelope())
        pairs = {(e["from"], e["to"]) for e in out["edges"]}
        self.assertEqual(pairs, {("M", "N"), ("M", "PKG")})

    def test_annotation_present_with_fixed_key_order(self) -> None:
        out = diet_envelope(self._envelope())
        self.assertTrue(out["neighbors_filtered"])
        self.assertEqual(tuple(out["omitted_neighbors"].keys()), OMISSION_REASONS)
        self.assertEqual(
            out["omitted_neighbors"],
            {"stdlib": 1, "unresolved": 1, "external_symbol": 1, "fanout_capped": 0},
        )

    def test_matches_and_other_keys_pass_through(self) -> None:
        env = self._envelope()
        env["degraded_match"] = "or_fallback"
        out = diet_envelope(env)
        self.assertEqual([m["id"] for m in out["matches"]], ["M"])
        self.assertEqual(out["degraded_match"], "or_fallback")

    def test_input_envelope_not_mutated(self) -> None:
        env = self._envelope()
        diet_envelope(env)
        self.assertEqual(len(env["neighbors"]), 5)
        self.assertNotIn("neighbors_filtered", env)


class DietEscapeHatchTest(unittest.TestCase):
    """Unit: ``full=True`` and error payloads pass through unchanged."""

    def test_full_returns_envelope_unchanged(self) -> None:
        env = {
            "query": "x",
            "matches": [_neighbor("M", "project")],
            "neighbors": [_neighbor("STD", "stdlib")],
            "edges": [{"from": "M", "to": "STD", "type": "calls"}],
        }
        out = diet_envelope(env, full=True)
        self.assertIs(out, env)
        self.assertNotIn("neighbors_filtered", out)

    def test_error_payload_passes_through(self) -> None:
        err = {"error": "node not found: nope"}
        out = diet_envelope(err)
        self.assertIs(out, err)
        self.assertNotIn("neighbors_filtered", out)


class DietCapTest(unittest.TestCase):
    """Unit: deterministic fan-out cap."""

    def _hub(self, n_project: int, cap_neighbors: list[dict]) -> dict:
        neighbors = [
            _neighbor(f"symbol:py:pkg:caller_{i:03d}", "project")
            for i in range(n_project)
        ] + cap_neighbors
        edges = [
            {"from": "M", "to": nb["id"], "type": "calls"} for nb in neighbors
        ]
        return {"query": "x", "matches": [_neighbor("M", "project")],
                "neighbors": neighbors, "edges": edges}

    def test_cap_bounds_neighbors_and_counts_overflow(self) -> None:
        out = diet_envelope(self._hub(6, []), cap=4)
        self.assertEqual(len(out["neighbors"]), 4)
        self.assertEqual(out["omitted_neighbors"]["fanout_capped"], 2)

    def test_capped_neighbors_stay_id_sorted(self) -> None:
        out = diet_envelope(self._hub(6, []), cap=4)
        ids = [n["id"] for n in out["neighbors"]]
        self.assertEqual(ids, sorted(ids))

    def test_cap_prefers_project_over_origin_less(self) -> None:
        # An origin-less node ranks after project nodes in the cap key, so with
        # a cap tighter than the project count it is the one dropped.
        extra = {"id": "doc:z", "type": "doc"}
        out = diet_envelope(self._hub(4, [extra]), cap=4)
        self.assertNotIn("doc:z", {n["id"] for n in out["neighbors"]})
        self.assertEqual(out["omitted_neighbors"]["fanout_capped"], 1)

    def test_default_cap_is_a_safety_valve_not_routine(self) -> None:
        # Under the default cap a normal-sized neighbor set is untouched.
        out = diet_envelope(self._hub(DEFAULT_NEIGHBOR_CAP - 5, []))
        self.assertEqual(out["omitted_neighbors"]["fanout_capped"], 0)


class DietContextAnchorTest(unittest.TestCase):
    """Unit: context envelope keeps the focal node; edges to it survive."""

    def test_focal_node_is_anchor(self) -> None:
        env = {
            "node": {"id": "F", "type": "file", "props": {"origin": "project"}},
            "neighbors": [
                _neighbor("N", "project"),
                _neighbor("STD", "stdlib"),
            ],
            "edges": [
                {"from": "F", "to": "N", "type": "calls"},
                {"from": "F", "to": "STD", "type": "calls"},
            ],
        }
        out = diet_envelope(env)
        self.assertEqual(out["node"]["id"], "F")
        self.assertEqual({n["id"] for n in out["neighbors"]}, {"N"})
        self.assertEqual(
            {(e["from"], e["to"]) for e in out["edges"]}, {("F", "N")}
        )


class DietDeterminismTest(unittest.TestCase):
    """Unit: same input -> byte-identical envelope (ADR 0012)."""

    def test_repeated_diet_is_byte_identical(self) -> None:
        env = {
            "query": "x",
            "matches": [_neighbor("M", "project")],
            "neighbors": [
                _neighbor(f"symbol:py:pkg:c_{i:03d}", "project") for i in range(8)
            ] + [_neighbor("STD", "stdlib")],
            "edges": [],
        }
        first = json.dumps(diet_envelope(env, cap=5), sort_keys=False)
        second = json.dumps(diet_envelope(env, cap=5), sort_keys=False)
        self.assertEqual(first, second)


def _write_hub_graph(root: Path, *, n_project: int, n_stdlib: int,
                     n_unresolved: int) -> str:
    """Materialize a graph: a focal file with a spray of typed callers.

    Returns the focal node id. Every caller points at the focal node via a
    ``calls`` edge so it lands in the focal node's 1-hop context.
    """
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    focal = "file:pkg/hub.py"
    nodes: dict[str, dict] = {
        focal: {"type": "file", "label": "hub.py",
                "props": {"origin": "project", "file": "pkg/hub.py"}},
    }
    edges = []

    def _add(node_id: str, origin: str, ntype: str = "symbol") -> None:
        nodes[node_id] = {"type": ntype, "label": node_id.rsplit(":", 1)[-1],
                          "props": {"origin": origin, "confidence": "definite"}}
        edges.append({"from": node_id, "to": focal, "type": "calls"})

    for i in range(n_project):
        _add(f"symbol:py:pkg.caller:fn_{i:03d}", "project")
    for i in range(n_stdlib):
        _add(f"symbol:py:os:std_{i:03d}", "stdlib")
    for i in range(n_unresolved):
        _add(f"symbol:unresolved:u_{i:03d}", "unresolved")
    payload = {"meta": {"version": 1, "schema_version": 1},
               "nodes": nodes, "edges": edges}
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    return focal


class _FixtureBase(unittest.TestCase):
    N_PROJECT = DEFAULT_NEIGHBOR_CAP + 10
    N_STDLIB = 30
    N_UNRESOLVED = 20

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self.root = Path(self._tmp)
        self.focal = _write_hub_graph(
            self.root, n_project=self.N_PROJECT, n_stdlib=self.N_STDLIB,
            n_unresolved=self.N_UNRESOLVED,
        )
        self._prev_refresh = os.environ.get("WELD_AUTO_REFRESH")
        os.environ["WELD_AUTO_REFRESH"] = "0"
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        import shutil

        if self._prev_refresh is None:
            os.environ.pop("WELD_AUTO_REFRESH", None)
        else:
            os.environ["WELD_AUTO_REFRESH"] = self._prev_refresh
        shutil.rmtree(self._tmp, ignore_errors=True)


class CliContextDietTest(_FixtureBase):
    """Integration: ``wd context`` over a hub is bounded + annotated."""

    def _run(self, *args: str) -> dict:
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_main(["--root", str(self.root), *args, "--no-refresh"])
        return json.loads(buf.getvalue())

    def test_default_context_is_bounded_and_annotated(self) -> None:
        env = self._run("context", self.focal, "--json")
        # stdlib + unresolved spray gone; project neighbors capped.
        self.assertEqual(len(env["neighbors"]), DEFAULT_NEIGHBOR_CAP)
        self.assertTrue(env["neighbors_filtered"])
        self.assertEqual(env["omitted_neighbors"]["stdlib"], self.N_STDLIB)
        self.assertEqual(env["omitted_neighbors"]["unresolved"], self.N_UNRESOLVED)
        self.assertEqual(env["omitted_neighbors"]["fanout_capped"], 10)
        # every surviving neighbor is a project node (no noise leaked)
        for n in env["neighbors"]:
            self.assertEqual(n["props"]["origin"], "project")

    def test_full_neighborhood_restores_everything(self) -> None:
        env = self._run("context", self.focal, "--json", "--full-neighborhood")
        total = self.N_PROJECT + self.N_STDLIB + self.N_UNRESOLVED
        self.assertEqual(len(env["neighbors"]), total)
        self.assertNotIn("neighbors_filtered", env)

    def test_default_context_is_deterministic(self) -> None:
        one = self._run("context", self.focal, "--json")
        two = self._run("context", self.focal, "--json")
        self.assertEqual(json.dumps(one), json.dumps(two))


class McpDietTest(_FixtureBase):
    """Integration: MCP ``weld_context`` applies the diet + escape hatch."""

    def _dispatch(self, tool: str, args: dict) -> dict:
        return mcp_dispatch(tool, args, root=self.root)

    def test_mcp_context_dieted_by_default(self) -> None:
        env = self._dispatch("weld_context", {"node_id": self.focal})
        self.assertEqual(len(env["neighbors"]), DEFAULT_NEIGHBOR_CAP)
        self.assertTrue(env["neighbors_filtered"])
        self.assertEqual(env["omitted_neighbors"]["stdlib"], self.N_STDLIB)
        self.assertEqual(env["omitted_neighbors"]["unresolved"], self.N_UNRESOLVED)

    def test_mcp_context_full_neighborhood_param(self) -> None:
        env = self._dispatch(
            "weld_context", {"node_id": self.focal, "full_neighborhood": True}
        )
        total = self.N_PROJECT + self.N_STDLIB + self.N_UNRESOLVED
        self.assertEqual(len(env["neighbors"]), total)
        self.assertNotIn("neighbors_filtered", env)


if __name__ == "__main__":
    unittest.main()
