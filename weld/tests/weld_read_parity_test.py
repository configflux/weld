"""CLI == MCP parity for the read surface (ADR 0083 thin-wrapper invariant).

All five agent-facing read commands must return the same answer on the CLI
(``--json``) and the MCP tool handler. ``query`` / ``context`` / ``brief`` route
through the one product read command (:mod:`weld.read`); ``callers`` / ``path``
call the same ``Graph`` method on both surfaces with no shaping. This pins the
byte-identity of the answer fields the ADR promises.

Scope notes:

* The comparison is at the MCP *handler* level (``mcp_server.weld_query`` etc.),
  before the dispatch layer stamps the transport-only ``freshness`` object and
  before ``children_status`` is attached at a federated root -- ADR 0083 rules
  those additive stamps as transport, not part of the shaped answer.
* ADR 0083 resolves the ADR 0078 speculative-match asymmetry *in favour of the
  CLI*: ``weld_query`` now runs the same speculative-match filter as ``wd
  query`` via :func:`weld.read.read_query`. So default-flag ``query`` is
  byte-identical on both surfaces and the unresolved sentinel leaves ``matches``
  (``test_query_default_drops_speculative_on_both_surfaces``); the shaping with
  that filter *bypassed* is also identical (``include_speculative``).
* Every shaped ``query`` / ``context`` envelope must carry the
  ``size_capped`` omission reason, proving both surfaces went through
  :mod:`weld.read` (and not the bare ADR 0078 diet).
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from weld import mcp_server
from weld._envelope_diet import OMISSION_REASONS
from weld._graph_cli import main as cli_main
from weld.brief import main as brief_main
from weld.read import SIZE_CAPPED_REASON

_TERM = "store"


def _write_graph(root: Path) -> None:
    """A fixture with a resolved match, an unresolved sentinel match, a project
    neighbor, and a stdlib neighbor the diet removes -- enough that shaping is
    observable and the query speculative-filter asymmetry is exercised."""
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    nodes = {
        "entity:Store": {
            "type": "entity", "label": "Store",
            "props": {"origin": "project", "confidence": "definite",
                      "file": "app/models.py"},
        },
        "symbol:py:app.store:save_store": {
            "type": "symbol", "label": "save_store",
            "props": {"origin": "project", "confidence": "definite",
                      "file": "app/store.py"},
        },
        "symbol:unresolved:store": {
            "type": "symbol", "label": "store",
            "props": {"origin": "unresolved", "confidence": "speculative",
                      "resolution": "unresolved"},
        },
        "symbol:py:os:getcwd": {
            "type": "symbol", "label": "getcwd",
            "props": {"origin": "stdlib", "confidence": "definite"},
        },
    }
    edges = [
        {"from": "symbol:py:app.store:save_store", "to": "entity:Store",
         "type": "references"},
        {"from": "entity:Store", "to": "symbol:py:os:getcwd", "type": "calls"},
    ]
    payload = {"meta": {"version": 1, "schema_version": 1},
               "nodes": nodes, "edges": edges}
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )


class CliMcpReadParityTest(unittest.TestCase):
    """The CLI ``--json`` envelope equals the MCP handler payload."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self.root = Path(self._tmp)
        _write_graph(self.root)
        self._prev = os.environ.get("WELD_AUTO_REFRESH")
        os.environ["WELD_AUTO_REFRESH"] = "0"
        # Drop any cross-test cached graph so this fixture is served fresh.
        from weld._mcp_read import clear_graph_cache
        clear_graph_cache()
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        import shutil

        from weld._mcp_read import clear_graph_cache
        clear_graph_cache()
        if self._prev is None:
            os.environ.pop("WELD_AUTO_REFRESH", None)
        else:
            os.environ["WELD_AUTO_REFRESH"] = self._prev
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _cli(self, *args: str) -> dict:
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_main(["--root", str(self.root), *args, "--no-refresh"])
        return json.loads(buf.getvalue())

    def _cli_brief(self, *args: str) -> dict:
        buf = io.StringIO()
        with redirect_stdout(buf):
            brief_main(["--root", str(self.root), *args, "--no-refresh"])
        return json.loads(buf.getvalue())

    def test_query_default_drops_speculative_on_both_surfaces(self) -> None:
        # The recorded behavior change (ADR 0083): with default flags the
        # unresolved sentinel leaves ``matches`` on the MCP surface too, so the
        # default CLI and default MCP answers are byte-identical.
        cli_env = self._cli("query", _TERM, "--limit", "20", "--json")
        mcp_env = mcp_server.weld_query(_TERM, limit=20, root=str(self.root))
        self.assertEqual(cli_env, mcp_env)
        for surface, env in (("cli", cli_env), ("mcp", mcp_env)):
            match_ids = {m["id"] for m in env["matches"]}
            self.assertIn("entity:Store", match_ids, surface)
            self.assertNotIn("symbol:unresolved:store", match_ids, surface)

    def test_query_include_speculative_parity(self) -> None:
        # Bypassing the filter on both surfaces still yields identical shaping,
        # and now the sentinel survives on both (the positive control).
        cli_env = self._cli(
            "query", _TERM, "--limit", "20", "--include-speculative", "--json",
        )
        mcp_env = mcp_server.weld_query(
            _TERM, limit=20, include_speculative=True, root=str(self.root),
        )
        self.assertEqual(cli_env, mcp_env)
        self.assertIn(
            "symbol:unresolved:store", {m["id"] for m in cli_env["matches"]},
        )
        self.assertIn(SIZE_CAPPED_REASON, cli_env["omitted_neighbors"])
        self.assertEqual(
            tuple(cli_env["omitted_neighbors"].keys()),
            OMISSION_REASONS + (SIZE_CAPPED_REASON,),
        )

    def test_context_cli_equals_mcp_handler(self) -> None:
        cli_env = self._cli("context", "entity:Store", "--json")
        mcp_env = mcp_server.weld_context("entity:Store", root=str(self.root))
        self.assertEqual(cli_env, mcp_env)
        self.assertIn(SIZE_CAPPED_REASON, cli_env["omitted_neighbors"])

    def test_brief_cli_equals_mcp_handler(self) -> None:
        cli_env = self._cli_brief(_TERM, "--limit", "20")
        mcp_env = mcp_server.weld_brief(_TERM, limit=20, root=str(self.root))
        self.assertEqual(cli_env, mcp_env)
        # Brief edges are de-dangled to emitted bucket nodes on both surfaces.
        node_ids = {
            n["id"]
            for bucket in ("primary", "interfaces", "docs", "build", "boundaries")
            for n in cli_env.get(bucket, [])
        }
        for edge in cli_env["edges"]:
            self.assertIn(edge["from"], node_ids)
            self.assertIn(edge["to"], node_ids)

    def test_full_size_parity(self) -> None:
        # The escape hatch is symmetric too: --full-size (CLI) == full_size (MCP).
        cli_env = self._cli(
            "context", "entity:Store", "--json", "--full-size",
        )
        mcp_env = mcp_server.weld_context(
            "entity:Store", full_size=True, root=str(self.root),
        )
        self.assertEqual(cli_env, mcp_env)

    def test_callers_cli_equals_mcp_handler(self) -> None:
        # callers has no shaping on either surface: both call Graph.callers, so
        # the answer must be identical. getcwd is called by Store (a `calls`
        # edge), so the result is non-degenerate.
        cli_env = self._cli("callers", "symbol:py:os:getcwd", "--json")
        mcp_env = mcp_server.weld_callers(
            "symbol:py:os:getcwd", root=str(self.root),
        )
        self.assertEqual(cli_env, mcp_env)
        self.assertIn(
            "entity:Store", {c["id"] for c in mcp_env.get("callers", [])},
        )

    def test_path_cli_equals_mcp_handler(self) -> None:
        # path has no shaping either; the single-repo MCP handler's
        # children_status attach is a no-op, so the answer must be identical.
        cli_env = self._cli(
            "path", "entity:Store", "symbol:py:os:getcwd", "--json",
        )
        mcp_env = mcp_server.weld_path(
            "entity:Store", "symbol:py:os:getcwd", root=str(self.root),
        )
        self.assertEqual(cli_env, mcp_env)
        self.assertNotIn("children_status", mcp_env)


if __name__ == "__main__":
    unittest.main()
