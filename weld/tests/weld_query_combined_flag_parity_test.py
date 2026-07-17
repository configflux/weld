"""End-to-end CLI test: combined escape hatches restore raw ``Graph.query``.

ADR 0078 (envelope diet, bd d1oc) composes two *independent* CLI surface
projections on top of the raw ``Graph.query`` envelope:

1. the speculative-match filter (drop ``origin=unresolved`` sentinel *matches*),
   bypassed by ``--include-speculative``;
2. the neighbor diet (drop stdlib/unresolved/speculative-external *neighbors*,
   cap fan-out, annotate omissions), bypassed by ``--full-neighborhood``.

The contract (``weld._query_surface.apply_query_envelope``) is that full raw
parity with ``Graph.query`` needs *both* flags together. The two hatches are
each covered in isolation elsewhere -- ``--include-speculative`` restores the
sentinel matches (``weld_query_include_speculative_test``) and
``--full-neighborhood`` restores the dieted neighbors
(``weld_envelope_diet_test``) -- and the *MCP* combined path is pinned by
``test_weld_query_full_neighborhood_matches_cli_helper``
(``weld_mcp_server_test``). This module pins the missing corner: byte-parity of
the **CLI** ``--json`` envelope with the raw core ``Graph.query`` result when
**both** flags are set, so a future reordering of the filter vs. the diet in
``apply_query_envelope`` cannot silently break combined-flag CLI parity.

The assertion mirrors the MCP parity test: full-dict ``assertEqual`` of the
CLI-emitted envelope (parsed from the ``--json`` output the CLI serializes via
``weld._graph_cli._out``) against ``Graph.query`` over the same term, limit, and
fixture. The fixture is deliberately non-degenerate -- it carries an unresolved
sentinel *match* (so dropping ``--include-speculative`` changes ``matches``) and
a stdlib *neighbor* the diet removes (so dropping ``--full-neighborhood`` changes
``neighbors``/``edges`` and adds the ``neighbors_filtered`` annotation) -- so the
parity check genuinely depends on both hatches, not just on one.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from weld._graph_cli import main as cli_main
from weld.graph import Graph

#: Query token shared by the resolved match and the unresolved sentinel.
_TERM = "widget"
#: Passed explicitly to both the CLI and ``Graph.query`` so the comparison is
#: never coupled to the CLI ``--limit`` default drifting from ``Graph.query``'s.
_LIMIT = 20

_SENTINEL_MATCH = "symbol:unresolved:widget"
_DIETED_NEIGHBOR = "symbol:py:os:dieted_neighbor"


def _write_graph(root: Path) -> None:
    """Materialize a graph exercising *both* CLI escape hatches at once.

    Mirrors the ``_write_edge_graph`` construction pattern of
    ``weld_query_include_speculative_test`` but tuned so each hatch has a
    distinct, observable effect:

    * ``make_widget`` -- resolved (``origin=project``) match of ``widget``;
    * ``widget`` -- unresolved sentinel *match* of ``widget`` (dropped by the
      default speculative-match filter, restored by ``--include-speculative``);
    * ``kept_neighbor`` -- project *neighbor* of ``make_widget`` the diet keeps;
    * ``dieted_neighbor`` -- stdlib *neighbor* of ``make_widget`` the diet drops
      (restored by ``--full-neighborhood``).

    Neither neighbor carries the ``widget`` token, so they only ever appear as
    neighbors, never as matches.
    """
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    nodes = {
        "symbol:py:pkg.mod:make_widget": {
            "type": "symbol", "label": "make_widget",
            "props": {"origin": "project", "confidence": "definite",
                      "file": "pkg/mod.py"},
        },
        _SENTINEL_MATCH: {
            "type": "symbol", "label": "widget",
            "props": {"origin": "unresolved", "confidence": "speculative",
                      "resolution": "unresolved"},
        },
        "symbol:py:pkg.mod:kept_neighbor": {
            "type": "symbol", "label": "kept_neighbor",
            "props": {"origin": "project", "confidence": "definite",
                      "file": "pkg/mod.py"},
        },
        _DIETED_NEIGHBOR: {
            "type": "symbol", "label": "dieted_neighbor",
            "props": {"origin": "stdlib", "confidence": "definite"},
        },
    }
    edges = [
        {"from": "symbol:py:pkg.mod:make_widget",
         "to": "symbol:py:pkg.mod:kept_neighbor", "type": "calls"},
        {"from": "symbol:py:pkg.mod:make_widget",
         "to": _DIETED_NEIGHBOR, "type": "calls"},
    ]
    payload = {"meta": {"version": 1, "schema_version": 1},
               "nodes": nodes, "edges": edges}
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )


class CombinedFlagRawParityTest(unittest.TestCase):
    """``--include-speculative --full-neighborhood`` == raw ``Graph.query``."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self.root = Path(self._tmp)
        _write_graph(self.root)
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        import shutil

        shutil.rmtree(self._tmp, ignore_errors=True)

    def _run(self, *args: str) -> str:
        """Drive the CLI in-process; ``--root`` precedes the subcommand and
        ``--no-refresh`` is a query-subparser flag (same invoker as the
        sibling include-speculative / diet CLI tests -- never shells out)."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_main(["--root", str(self.root), *args, "--no-refresh"])
        return buf.getvalue()

    def test_combined_flags_are_byte_identical_to_raw_query(self) -> None:
        g = Graph(self.root)
        g.load()
        raw = g.query(_TERM, _LIMIT)

        # The fixture must be non-degenerate for the parity check to mean
        # anything: the raw envelope has to carry the sentinel *match* (the
        # speculative filter's target) and the stdlib *neighbor* (the diet's
        # target). Otherwise both hatches would be no-ops and parity would hold
        # trivially, silently weakening this guard.
        self.assertIn(_SENTINEL_MATCH, {m["id"] for m in raw["matches"]})
        self.assertIn(_DIETED_NEIGHBOR, {n["id"] for n in raw["neighbors"]})

        cli_env = json.loads(
            self._run(
                "query", _TERM, "--limit", str(_LIMIT),
                "--include-speculative", "--full-neighborhood", "--json",
            )
        )
        # Byte-parity: the full CLI --json envelope equals the raw core
        # Graph.query dict (same normalization as the MCP parity test's
        # assertEqual(result, Graph.query(...))). Needs BOTH flags: dropping
        # --include-speculative removes the sentinel from ``matches``; dropping
        # --full-neighborhood removes the stdlib neighbor and adds the diet
        # annotation -- either makes this assertion fail.
        self.assertEqual(cli_env, raw)


if __name__ == "__main__":
    unittest.main()
