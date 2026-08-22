"""CLI == MCP parity for the bounded traversal reads (ADR 0082 + ADR 0083).

The sibling of ``weld_read_parity_test`` (query / context / brief / path /
stale), covering the four reads this change brings under the byte budget:
``impact`` / ``callers`` / ``references`` / ``trace``. Two things are pinned
here that unit tests over a synthetic envelope cannot reach:

1. **Parity.** The budget lives in core (:mod:`weld.read_traversal`), not at
   the MCP boundary, so ``wd <cmd> --json`` and the matching tool handler must
   return the same bytes -- with and without ``--full-size``.
2. **The cap actually fires end to end.** The fixture is deliberately built
   past :data:`weld._read_budget.DEFAULT_READ_BUDGET_BYTES`, so these assert
   the *default* budget bounds a real command invocation, not a test-only one.

Comparison is at the MCP handler level, before the dispatch layer stamps the
transport-only ``freshness`` object, matching ``weld_read_parity_test``.
``wd impact``'s ``warnings.stale_graph`` is excluded: it records the CLI's
``--allow-stale`` gate, which has no MCP counterpart (the MCP path
auto-refreshes instead), and it predates this change.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from weld import mcp_server
from weld._graph_cli import main as cli_main
from weld._read_budget import DEFAULT_READ_BUDGET_BYTES, envelope_bytes
from weld.contract import SCHEMA_VERSION
from weld.impact_cli import main as impact_main
from weld.trace import main as trace_main

#: Enough fat dependents that the default 64 KiB budget fires on every surface.
_FANOUT = 220
_SEED = "file:app/core.py"


def _write_big_graph(root: Path) -> None:
    """A hub file with a wide reverse-dependency fan-out.

    Shaped like the real report: one hot module that many others import and
    call, so ``impact`` / ``callers`` / ``references`` all overflow on it.
    Props are padded to node sizes typical of a discovered graph (import
    lists, exports) rather than to an artificial extreme.
    """
    nodes: dict[str, dict] = {
        _SEED: {
            "type": "file", "label": "app/core.py",
            "props": {"file": "app/core.py", "language": "python",
                      "origin": "project", "confidence": "definite"},
        },
        "symbol:py:app.core:helper": {
            "type": "symbol", "label": "helper",
            "props": {"file": "app/core.py", "module": "app.core",
                      "qualname": "helper", "language": "python",
                      "origin": "project", "confidence": "definite"},
        },
        "service:api": {
            "type": "service", "label": "api",
            "props": {"file": "app/api.py", "origin": "project",
                      "confidence": "definite"},
        },
    }
    edges: list[dict] = []
    for i in range(_FANOUT):
        file_id = f"file:app/mod{i}.py"
        sym_id = f"symbol:py:app.mod{i}:helper"
        nodes[file_id] = {
            "type": "file", "label": f"app/mod{i}.py",
            "props": {
                "file": f"app/mod{i}.py", "language": "python",
                "origin": "project", "confidence": "definite",
                "imports_from": ["app.core", "json", "pathlib", "typing"],
                "exports": [f"handler_{i}", f"build_{i}", f"render_{i}"],
                "constants": [f"MOD_{i}_DEFAULT", f"MOD_{i}_LIMIT"],
            },
        }
        nodes[sym_id] = {
            "type": "symbol", "label": "helper",
            "props": {
                "file": f"app/mod{i}.py", "module": f"app.mod{i}",
                "qualname": "helper", "language": "python",
                "origin": "project", "confidence": "definite",
            },
        }
        edges.append({"from": file_id, "to": _SEED, "type": "depends_on",
                      "props": {"confidence": "definite"}})
        edges.append({"from": sym_id, "to": "symbol:py:app.core:helper",
                      "type": "calls", "props": {"confidence": "definite"}})
        edges.append({"from": file_id, "to": sym_id, "type": "contains",
                      "props": {"confidence": "definite"}})
        edges.append({"from": "service:api", "to": file_id, "type": "depends_on",
                      "props": {"confidence": "definite"}})
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    (root / ".weld" / "graph.json").write_text(
        json.dumps({
            "meta": {"version": SCHEMA_VERSION, "git_sha": "deadbeef",
                     "updated_at": "2026-08-14T00:00:00+00:00"},
            "nodes": nodes, "edges": edges,
        }),
        encoding="utf-8",
    )


class TraversalParityTest(unittest.TestCase):
    """``wd <cmd> --json`` equals the MCP handler payload, bounded or not."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self.root = Path(self._tmp)
        _write_big_graph(self.root)
        self._prev = os.environ.get("WELD_AUTO_REFRESH")
        os.environ["WELD_AUTO_REFRESH"] = "0"
        from weld._mcp_read import clear_graph_cache
        clear_graph_cache()
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
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

    def _cli_impact(self, *args: str) -> dict:
        buf = io.StringIO()
        with redirect_stdout(buf):
            impact_main([*args, "--root", str(self.root), "--json",
                         "--allow-stale", "--no-refresh"])
        payload = json.loads(buf.getvalue())
        payload["warnings"].pop("stale_graph", None)
        return payload

    def _mcp_impact(self, target: str, **kwargs) -> dict:
        payload = mcp_server.weld_impact(target, root=str(self.root), **kwargs)
        payload["warnings"].pop("stale_graph", None)
        return payload

    def _cli_trace(self, *args: str) -> dict:
        buf = io.StringIO()
        with redirect_stdout(buf):
            trace_main([*args, "--root", str(self.root), "--no-refresh"])
        return json.loads(buf.getvalue())

    # ---- impact ---------------------------------------------------------

    def test_impact_cli_equals_mcp_and_fits_the_budget(self) -> None:
        cli = self._cli_impact("app/core.py")
        mcp = self._mcp_impact("app/core.py")
        self.assertEqual(cli, mcp)
        self.assertLessEqual(envelope_bytes(cli), DEFAULT_READ_BUDGET_BYTES)
        report = cli["warnings"]["size_capped"]
        dropped = sum(
            sum(v.values()) if isinstance(v, dict) else v
            for k, v in report.items() if k != "edges"
        )
        self.assertGreater(
            dropped, 0,
            "fixture must exceed the default budget or this pins nothing",
        )
        self.assertIs(cli["warnings"]["budget_exceeded"], False)

    def test_impact_surface_counts_are_identical_on_both_surfaces(self) -> None:
        """bd gfpl: the count is the safety-bearing field, so parity covers it.

        Surface *members* are prunable now, so a surface that shaped differently
        on the two paths would show up here as a differing count -- which is
        exactly the divergence ADR 0083 exists to forbid.
        """
        cli = self._cli_impact("app/core.py")
        mcp = self._mcp_impact("app/core.py")
        full = self._cli_impact("app/core.py", "--full-size")
        self.assertEqual(cli["affected_surface_counts"], mcp["affected_surface_counts"])
        self.assertEqual(
            cli["affected_surface_counts"],
            {name: len(items) for name, items in full["affected_surfaces"].items()},
        )

    def test_impact_full_size_parity_and_is_unbounded(self) -> None:
        cli = self._cli_impact("app/core.py", "--full-size")
        mcp = self._mcp_impact("app/core.py", full_size=True)
        self.assertEqual(cli, mcp)
        self.assertGreater(envelope_bytes(cli), DEFAULT_READ_BUDGET_BYTES)

    def test_impact_risk_verdict_is_identical_bounded_or_not(self) -> None:
        """A payload that was merely too big must not report a smaller radius."""
        bounded = self._cli_impact("app/core.py")
        full = self._cli_impact("app/core.py", "--full-size")
        self.assertEqual(bounded["risk_level"], full["risk_level"])
        # The member lists may shrink under the budget (bd gfpl); the counts
        # they report may not, and the counts are what carry the radius.
        self.assertEqual(
            bounded["affected_surface_counts"],
            {name: len(items) for name, items in full["affected_surfaces"].items()},
        )

    def test_impact_human_output_reports_the_true_counts(self) -> None:
        """The terminal has no tool cap, so the summary stays unbounded."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            impact_main(["app/core.py", "--root", str(self.root),
                         "--allow-stale", "--no-refresh"])
        full = self._cli_impact("app/core.py", "--full-size")
        expected = len(full["direct_dependents"])
        self.assertIn(f"Direct dependents: {expected}", buf.getvalue())

    # ---- callers --------------------------------------------------------

    def test_callers_cli_equals_mcp_and_fits_the_budget(self) -> None:
        cli = self._cli("callers", "symbol:py:app.core:helper", "--json")
        mcp = mcp_server.weld_callers(
            "symbol:py:app.core:helper", root=str(self.root),
        )
        self.assertEqual(cli, mcp)
        self.assertLessEqual(envelope_bytes(cli), DEFAULT_READ_BUDGET_BYTES)
        self.assertGreater(cli["size_capped"]["callers"], 0)

    def test_callers_full_size_parity_and_is_unbounded(self) -> None:
        cli = self._cli("callers", "symbol:py:app.core:helper", "--json",
                        "--full-size")
        mcp = mcp_server.weld_callers(
            "symbol:py:app.core:helper", full_size=True, root=str(self.root),
        )
        self.assertEqual(cli, mcp)
        self.assertEqual(len(cli["callers"]), _FANOUT)

    # ---- references -----------------------------------------------------

    def test_references_cli_equals_mcp_and_fits_the_budget(self) -> None:
        cli = self._cli("references", "helper", "--json")
        mcp = mcp_server.weld_references("helper", root=str(self.root))
        self.assertEqual(cli, mcp)
        self.assertLessEqual(envelope_bytes(cli), DEFAULT_READ_BUDGET_BYTES)
        self.assertGreater(cli["size_capped"]["callers"], 0)

    def test_references_full_size_parity(self) -> None:
        cli = self._cli("references", "helper", "--json", "--full-size")
        mcp = mcp_server.weld_references(
            "helper", full_size=True, root=str(self.root),
        )
        self.assertEqual(cli, mcp)
        self.assertGreater(envelope_bytes(cli), DEFAULT_READ_BUDGET_BYTES)

    # ---- trace ----------------------------------------------------------

    def test_trace_cli_equals_mcp_handler(self) -> None:
        cli = self._cli_trace("--node", "service:api")
        mcp = mcp_server.weld_trace(node_id="service:api", root=str(self.root))
        self.assertEqual(cli, mcp)
        self.assertLessEqual(envelope_bytes(cli), DEFAULT_READ_BUDGET_BYTES)

    def test_trace_full_size_parity(self) -> None:
        cli = self._cli_trace("--node", "service:api", "--full-size")
        mcp = mcp_server.weld_trace(
            node_id="service:api", full_size=True, root=str(self.root),
        )
        self.assertEqual(cli, mcp)

    # ---- surface completeness -------------------------------------------

    def test_every_bounded_read_tool_advertises_full_size(self) -> None:
        """A bounded surface with no escape hatch is a dead end for the caller."""
        bounded = {
            "weld_query", "weld_context", "weld_brief",
            "weld_impact", "weld_callers", "weld_references", "weld_trace",
        }
        advertised = {
            tool.name for tool in mcp_server.build_tools()
            if "full_size" in tool.input_schema.get("properties", {})
        }
        self.assertEqual(bounded - advertised, set())


if __name__ == "__main__":
    unittest.main()
