"""Dispatched bytes, not handler bytes, fit the budget (ADR 0082, bd hwwo).

ADR 0082's byte budget used to bound the payload a *handler* returns.
``weld._mcp_dispatch`` then stamps the additive ``freshness`` object on top
of every read in :data:`weld._mcp_read.FRESHNESS_TOOLS` -- after the handler
already shaped its answer -- so the bytes actually delivered to the client
could exceed the budget by the size of the stamp. This module is the
acceptance test for the fix: it dispatches each of the seven read tools bd
hwwo measured (``weld_query``, ``weld_context``, ``weld_brief``,
``weld_callers``, ``weld_references``, ``weld_trace``, ``weld_impact``)
through the *real* MCP dispatch path -- handler shaping, freshness stamp,
telemetry, all of it -- against a fixture built to push the pre-stamp
shaping close to its own ceiling, and asserts the bytes the client actually
receives still fit :data:`weld._read_budget.DEFAULT_READ_BUDGET_BYTES`.

``weld_path`` is excluded: it carries the freshness stamp too, but ADR 0082
exempts it from byte-budget shaping altogether (bounded by graph diameter,
never observed to overflow), so it is not one of the seven bd hwwo measured.

The federated ``children_status`` half of the same fix -- unbounded in child
count, so a fixed reserve alone cannot cover it -- has its own dedicated
fixture in :mod:`weld.tests.weld_mcp_children_status_budget_test`.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from functools import partial
from pathlib import Path

from weld._mcp_dispatch import dispatch as _dispatch
from weld._read_budget import DEFAULT_READ_BUDGET_BYTES, envelope_bytes
from weld.contract import SCHEMA_VERSION
from weld.mcp_server import build_tools

# _mcp_dispatch.dispatch now takes the live tool registry as an explicit
# tools_provider parameter (ADR 0130 disposition #7: dependency-injected by
# weld.mcp_server, the composition root, instead of imported back). This
# test dispatches through the real path, so it supplies the real registry --
# bound once here rather than at every call site below.
dispatch = partial(_dispatch, tools_provider=build_tools)

_FANOUT = 300
_SEED = "file:app/core.py"
_HELPER = "symbol:py:app.core:helper"


def _write_big_graph(root: Path) -> None:
    """A hub file with a wide reverse-dependency fan-out.

    Shaped like the real bd 5tzx/b44q report: one hot module many others
    import and call, so ``impact`` / ``callers`` / ``references`` overflow
    their own shaping ceiling on it and the byte budget has to actually
    prune (not just pass an already-small payload through).
    """
    nodes: dict[str, dict] = {
        _SEED: {
            "type": "file", "label": "app/core.py",
            "props": {"file": "app/core.py", "language": "python",
                      "origin": "project", "confidence": "definite"},
        },
        _HELPER: {
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
                "imports_from": ["app.core", "json", "pathlib", "typing",
                                  "collections", "itertools"],
                "exports": [f"handler_{i}", f"build_{i}", f"render_{i}",
                            f"parse_{i}", f"format_{i}"],
                "constants": [f"MOD_{i}_DEFAULT", f"MOD_{i}_LIMIT",
                              f"MOD_{i}_MAX", f"MOD_{i}_MIN"],
            },
        }
        nodes[sym_id] = {
            "type": "symbol", "label": "helper",
            "props": {"file": f"app/mod{i}.py", "module": f"app.mod{i}",
                      "qualname": "helper", "language": "python",
                      "origin": "project", "confidence": "definite"},
        }
        edges.append({"from": file_id, "to": _SEED, "type": "depends_on",
                      "props": {"confidence": "definite"}})
        edges.append({"from": sym_id, "to": _HELPER,
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


class DispatchedBytesFitTheBudgetTest(unittest.TestCase):
    """The client-received bytes, not the handler's, are what must fit."""

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

    def _dispatched(self, tool_name: str, args: dict) -> dict:
        result = dispatch(tool_name, args, root=str(self.root))
        self.assertIn("freshness", result, f"{tool_name} must carry the stamp")
        return result

    # The seven tools bd hwwo measured, with arguments chosen so each one's
    # own shaping is genuinely exercised against the fixture above.
    _CASES: tuple[tuple[str, dict], ...] = (
        ("weld_query", {"term": "core", "limit": 20}),
        ("weld_context", {"node_id": _SEED}),
        ("weld_brief", {"area": "mod", "limit": 80}),
        ("weld_callers", {"symbol_id": _HELPER}),
        ("weld_references", {"symbol_name": "helper"}),
        ("weld_trace", {"node_id": "service:api"}),
        ("weld_impact", {"target": "app/core.py"}),
    )

    def test_every_measured_tool_fits_the_budget_once_dispatched(self) -> None:
        for tool_name, args in self._CASES:
            with self.subTest(tool=tool_name):
                result = self._dispatched(tool_name, args)
                self.assertLessEqual(
                    envelope_bytes(result), DEFAULT_READ_BUDGET_BYTES,
                    f"{tool_name}: dispatched bytes exceeded the budget "
                    f"the client was promised",
                )

    def test_the_fixture_actually_exercises_the_traversal_budget(self) -> None:
        """A meaningful pin, not a vacuous one: the cap must have fired.

        If nothing here ever engaged the byte budget, the test above would
        pass on a fixture too small to reproduce bd hwwo's gap at all.
        """
        callers = self._dispatched("weld_callers", {"symbol_id": _HELPER})
        self.assertGreater(callers["size_capped"]["callers"], 0)

        references = self._dispatched(
            "weld_references", {"symbol_name": "helper"},
        )
        self.assertGreater(references["size_capped"]["callers"], 0)

        impact = self._dispatched("weld_impact", {"target": "app/core.py"})
        total_dropped = sum(
            sum(v.values()) if isinstance(v, dict) else v
            for k, v in impact["warnings"]["size_capped"].items() if k != "edges"
        )
        self.assertGreater(total_dropped, 0)

    def test_dispatched_bytes_exceed_the_handlers_own_bytes_by_the_stamp(self) -> None:
        """The gap the fix closes: dispatch adds bytes a handler-only check
        would never see. Pinning that the delta is positive (not just that
        the total fits) keeps this test honest about what it is proving."""
        from weld import mcp_server

        handlers = {tool.name: tool.handler for tool in mcp_server.build_tools()}
        for tool_name, args in self._CASES:
            with self.subTest(tool=tool_name):
                handler_bytes = envelope_bytes(
                    handlers[tool_name](**args, root=str(self.root)),
                )
                dispatched_bytes = envelope_bytes(
                    self._dispatched(tool_name, args),
                )
                self.assertGreater(dispatched_bytes, handler_bytes)
                self.assertLessEqual(dispatched_bytes, DEFAULT_READ_BUDGET_BYTES)


if __name__ == "__main__":
    unittest.main()
