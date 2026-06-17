"""Curated architecture-overview slice for the cold-open viz view (ADR 0073).

The default `wd viz` overview used to sort every node by
``overview_key`` and keep the top 300, which on a real graph filled the
view with packages + files and stripped the agent-graph anchors
(commands / agents) as ``unresolved``. ADR 0073 replaces that with a
curated architecture slice: orientation anchors (commands, agents,
workflows, project packages, services, routes) plus the top files of
each project package, bounded well under the node cap so the cold open
is never truncated.

These tests pin the pure helper (``architecture_overview_ids``) and its
integration through ``normalize_graph_data``'s default-overview branch.
"""

from __future__ import annotations

import unittest
from collections import Counter

from weld.viz._adapter_helpers import (
    OVERVIEW_FILES_PER_PACKAGE,
    OVERVIEW_SLICE_LIMIT,
    architecture_overview_ids,
    is_entry_point,
)
from weld.viz._search import top_degree_suggestions
from weld.viz.adapter import normalize_graph_data


def _project(props: dict | None = None) -> dict:
    body = {"origin": "project"}
    if props:
        body.update(props)
    return body


class ArchitectureOverviewIdsTest(unittest.TestCase):
    """Unit coverage for the pure ``architecture_overview_ids`` helper."""

    def _anchor_graph(self) -> tuple[dict, list[dict]]:
        # Two project packages, one stdlib package (noise), command +
        # agent anchors (unresolved origin, agent-graph), and files
        # owned by each project package with distinct degrees.
        nodes = {
            "command:plan": {"type": "command", "label": "/plan", "props": {}},
            "agent:tdd": {"type": "agent", "label": "tdd", "props": {}},
            "workflow:ci": {"type": "workflow", "label": "ci", "props": {}},
            "package:python:weld": {
                "type": "package", "label": "weld", "props": _project(),
            },
            "package:python:tools": {
                "type": "package", "label": "tools", "props": _project(),
            },
            "package:python:os": {
                "type": "package", "label": "os", "props": {"origin": "stdlib"},
            },
            "file:weld/a": {"type": "file", "label": "a", "props": _project()},
            "file:weld/b": {"type": "file", "label": "b", "props": _project()},
            "file:weld/c": {"type": "file", "label": "c", "props": _project()},
            "file:weld/d": {"type": "file", "label": "d", "props": _project()},
            "file:tools/t": {"type": "file", "label": "t", "props": _project()},
            "symbol:weld:helper": {
                "type": "symbol", "label": "helper", "props": _project(),
            },
        }
        # weld package contains a..d; give them descending degree so the
        # top-N-per-package ordering is observable. tools contains t.
        edges = [
            {"from": "package:python:weld", "to": "file:weld/a", "type": "contains", "props": {}},
            {"from": "package:python:weld", "to": "file:weld/b", "type": "contains", "props": {}},
            {"from": "package:python:weld", "to": "file:weld/c", "type": "contains", "props": {}},
            {"from": "package:python:weld", "to": "file:weld/d", "type": "contains", "props": {}},
            {"from": "package:python:tools", "to": "file:tools/t", "type": "contains", "props": {}},
            # Degree padding: a > b > c > d via extra calls edges.
            {"from": "file:weld/a", "to": "symbol:weld:helper", "type": "calls", "props": {}},
            {"from": "file:weld/a", "to": "file:weld/b", "type": "calls", "props": {}},
            {"from": "file:weld/a", "to": "file:weld/c", "type": "calls", "props": {}},
            {"from": "file:weld/b", "to": "file:weld/c", "type": "calls", "props": {}},
            {"from": "file:weld/b", "to": "file:weld/d", "type": "calls", "props": {}},
            {"from": "file:weld/c", "to": "file:weld/d", "type": "calls", "props": {}},
        ]
        return nodes, edges

    def test_includes_agent_graph_anchors_despite_unresolved_origin(self) -> None:
        nodes, edges = self._anchor_graph()
        ids = set(architecture_overview_ids(nodes, edges, limit=OVERVIEW_SLICE_LIMIT))
        # Commands / agents / workflows are the orientation surfaces even
        # though classify_node() tags them unresolved (no props.origin).
        self.assertIn("command:plan", ids)
        self.assertIn("agent:tdd", ids)
        self.assertIn("workflow:ci", ids)

    def test_prefers_project_packages_over_stdlib(self) -> None:
        nodes, edges = self._anchor_graph()
        ids = set(architecture_overview_ids(nodes, edges, limit=OVERVIEW_SLICE_LIMIT))
        self.assertIn("package:python:weld", ids)
        self.assertIn("package:python:tools", ids)
        # The stdlib package is orientation noise -- excluded.
        self.assertNotIn("package:python:os", ids)

    def test_caps_files_per_package_to_highest_degree(self) -> None:
        nodes, edges = self._anchor_graph()
        ids = architecture_overview_ids(nodes, edges, limit=OVERVIEW_SLICE_LIMIT)
        weld_files = [i for i in ids if i.startswith("file:weld/")]
        # Only the top OVERVIEW_FILES_PER_PACKAGE (3) of weld's 4 files.
        self.assertEqual(len(weld_files), OVERVIEW_FILES_PER_PACKAGE)
        # a(deg5) > b(deg4) > c(deg4)|... d is the lowest-degree, dropped.
        self.assertIn("file:weld/a", weld_files)
        self.assertNotIn("file:weld/d", weld_files)

    def test_excludes_raw_symbols(self) -> None:
        nodes, edges = self._anchor_graph()
        ids = set(architecture_overview_ids(nodes, edges, limit=OVERVIEW_SLICE_LIMIT))
        self.assertNotIn("symbol:weld:helper", ids)

    def test_respects_overall_limit(self) -> None:
        nodes, edges = self._anchor_graph()
        ids = architecture_overview_ids(nodes, edges, limit=3)
        self.assertLessEqual(len(ids), 3)
        # Determinism: same input, same prefix order.
        self.assertEqual(ids, architecture_overview_ids(nodes, edges, limit=3))

    def test_falls_back_to_overview_key_when_no_anchors(self) -> None:
        # A graph with no anchor-type nodes at all (only bare symbols)
        # must still yield a non-empty slice via the overview_key fallback.
        nodes = {
            f"symbol:s{i}": {"type": "symbol", "label": str(i), "props": _project()}
            for i in range(5)
        }
        ids = architecture_overview_ids(nodes, [], limit=OVERVIEW_SLICE_LIMIT)
        self.assertTrue(ids)
        self.assertEqual(set(ids), set(nodes))


class DefaultOverviewIntegrationTest(unittest.TestCase):
    """``normalize_graph_data`` default overview routes through the slice."""

    def _graph(self) -> dict:
        nodes = {
            "command:plan": {"type": "command", "label": "/plan", "props": {}},
            "agent:tdd": {"type": "agent", "label": "tdd", "props": {}},
            "package:python:weld": {
                "type": "package", "label": "weld", "props": _project(),
            },
        }
        # Many project files + symbols to prove the curated cap, not the
        # 300-node cut, governs the cold open.
        for i in range(400):
            nodes[f"file:weld/f{i}"] = {
                "type": "file", "label": f"f{i}", "props": _project(),
            }
        for i in range(400):
            nodes[f"symbol:weld:s{i}"] = {
                "type": "symbol", "label": f"s{i}", "props": _project(),
            }
        edges = [
            {"from": "package:python:weld", "to": f"file:weld/f{i}", "type": "contains", "props": {}}
            for i in range(400)
        ]
        return {"nodes": nodes, "edges": edges}

    def test_default_overview_is_curated_and_not_truncated(self) -> None:
        payload = normalize_graph_data(self._graph())
        visible = payload["elements"]["nodes"]
        self.assertLessEqual(len(visible), OVERVIEW_SLICE_LIMIT)
        # The whole curated set fits -> cold open is never "capped".
        self.assertFalse(payload["truncated"]["nodes"])

    def test_default_overview_surfaces_anchors_not_raw_symbols(self) -> None:
        payload = normalize_graph_data(self._graph())
        ids = {n["data"]["id"] for n in payload["elements"]["nodes"]}
        types = dict(Counter(n["data"]["type"] for n in payload["elements"]["nodes"]))
        self.assertIn("command:plan", ids)
        self.assertIn("agent:tdd", ids)
        self.assertIn("package:python:weld", ids)
        # Bare symbols never reach the curated cold open.
        self.assertEqual(types.get("symbol", 0), 0)

    def test_explicit_node_types_bypasses_curated_slice(self) -> None:
        # Pinning node_types must keep the prior all-graph behavior so a
        # power user asking for symbols still gets every symbol.
        payload = normalize_graph_data(self._graph(), node_types={"symbol"})
        types = {n["data"]["type"] for n in payload["elements"]["nodes"]}
        self.assertEqual(types, {"symbol"})

    def test_explicit_requested_ids_bypasses_curated_slice(self) -> None:
        # A query / context slice supplies requested_node_ids and must be
        # rendered verbatim (curated path is overview-only).
        payload = normalize_graph_data(
            self._graph(), requested_node_ids=["symbol:weld:s0", "file:weld/f0"],
        )
        ids = {n["data"]["id"] for n in payload["elements"]["nodes"]}
        self.assertEqual(ids, {"symbol:weld:s0", "file:weld/f0"})


class EntryPointSeedTest(unittest.TestCase):
    """``is_entry_point`` + diversified ``top_degree_suggestions`` (ADR 0073)."""

    def test_is_entry_point_includes_agent_graph_anchors(self) -> None:
        # CLI commands / agents / mcp-servers are entry points even with
        # no origin tag (they classify as unresolved).
        self.assertTrue(is_entry_point("command:plan", {"type": "command"}))
        self.assertTrue(is_entry_point("agent:tdd", {"type": "agent"}))
        self.assertTrue(is_entry_point("mcp-server:weld", {"type": "mcp-server"}))

    def test_is_entry_point_includes_project_symbols(self) -> None:
        # A project-origin symbol is still an entry-point candidate (the
        # bd 123p behavior the search dropdown relies on).
        self.assertTrue(
            is_entry_point("symbol:weld:f", {"type": "symbol", "props": _project()})
        )

    def test_is_entry_point_excludes_unresolved_symbol_hubs(self) -> None:
        # Test-assertion hubs (unresolved symbols) are the noise bd 123p
        # exists to drop -- they are not anchor types, so excluded.
        self.assertFalse(
            is_entry_point(
                "symbol:unresolved:assertEqual",
                {"type": "symbol", "props": {"origin": "unresolved"}},
            )
        )

    def test_is_entry_point_excludes_stdlib_package(self) -> None:
        self.assertFalse(
            is_entry_point(
                "package:python:os", {"type": "package", "props": {"origin": "stdlib"}}
            )
        )

    def test_seed_surfaces_commands_not_only_agents(self) -> None:
        # Regression for the cold-open seed: a project with many agents
        # plus a command and a package must surface all three kinds, not
        # a list filled entirely with the highest-priority agents.
        nodes = {f"agent:a{i}": {"type": "agent", "label": f"a{i}", "props": {}} for i in range(6)}
        nodes["command:plan"] = {"type": "command", "label": "/plan", "props": {}}
        nodes["package:python:weld"] = {
            "type": "package", "label": "weld", "props": _project(),
        }
        data = {"nodes": nodes, "edges": []}
        ids = [s["id"] for s in top_degree_suggestions(data, 4)]
        types = {nodes[i]["type"] for i in ids}
        self.assertIn("command", types)
        self.assertIn("package", types)
        self.assertIn("agent", types)


if __name__ == "__main__":
    unittest.main()
