"""Tests for deterministic graph-community analysis."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.graph_communities import build_graph_communities  # noqa: E402


def _node(node_type: str, label: str, **props: object) -> dict:
    return {"type": node_type, "label": label, "props": dict(props)}


def _edge(src: str, dst: str, edge_type: str, **props: object) -> dict:
    return {"from": src, "to": dst, "type": edge_type, "props": dict(props)}


def _community_by_member(payload: dict, node_id: str) -> dict:
    cid = payload["assignments"][node_id]
    for community in payload["communities"]:
        if community["id"] == cid:
            return community
    raise AssertionError(f"community {cid} not reported")


class GraphCommunityTopologyTest(unittest.TestCase):
    def test_dense_clusters_keep_stable_ids_hubs_and_bridge_edges(self) -> None:
        nodes = {
            "service:a": _node("service", "A Service", language="python"),
            "symbol:py:a.entry": _node("symbol", "a.entry", language="python", file="a.py"),
            "symbol:py:a.helper": _node("symbol", "a.helper", language="python", file="a.py"),
            "symbol:py:a.repo": _node("symbol", "a.repo", language="python", file="a.py"),
            "service:b": _node("service", "B Service", language="typescript"),
            "symbol:ts:b.entry": _node("symbol", "b.entry", language="typescript", file="b.ts"),
            "symbol:ts:b.helper": _node("symbol", "b.helper", language="typescript", file="b.ts"),
            "symbol:ts:b.repo": _node("symbol", "b.repo", language="typescript", file="b.ts"),
        }
        edges = [
            _edge("service:a", "symbol:py:a.entry", "contains"),
            _edge("symbol:py:a.entry", "symbol:py:a.helper", "calls"),
            _edge("symbol:py:a.entry", "symbol:py:a.repo", "calls"),
            _edge("symbol:py:a.helper", "symbol:py:a.repo", "depends_on"),
            _edge("service:b", "symbol:ts:b.entry", "contains"),
            _edge("symbol:ts:b.entry", "symbol:ts:b.helper", "calls"),
            _edge("symbol:ts:b.entry", "symbol:ts:b.repo", "calls"),
            _edge("symbol:ts:b.helper", "symbol:ts:b.repo", "depends_on"),
            _edge("symbol:py:a.repo", "symbol:ts:b.repo", "relates_to"),
        ]

        payload = build_graph_communities({"nodes": nodes, "edges": edges}, top=12)

        self.assertEqual(payload["summary"]["total_communities"], 2)
        self.assertEqual(payload["assignments"]["service:a"], "c001")
        self.assertEqual(payload["assignments"]["service:b"], "c002")
        first = payload["communities"][0]
        self.assertEqual(first["id"], "c001")
        self.assertEqual(first["hub_nodes"][0]["id"], "symbol:py:a.entry")
        self.assertEqual(first["boundary_edges"], 1)
        self.assertEqual(first["boundary_links"][0]["other_community"], "c002")

    def test_mixed_language_summary_includes_csharp_and_cpp(self) -> None:
        nodes = {
            "entrypoint:cs:program": _node("entrypoint", "Program", language="C#", file="Program.cs"),
            "service:cs:app": _node("service", "AppService", language="csharp", file="AppService.cs"),
            "boundary:cs:http": _node("boundary", "HTTP boundary", language="csharp"),
            "symbol:cpp:main": _node("symbol", "main", language="C++", file="src/main.cpp"),
            "symbol:cpp:driver": _node("symbol", "Driver", language="cpp", file="src/driver.cpp"),
            "file:src/driver.hpp": _node("file", "driver.hpp", file="src/driver.hpp"),
        }
        edges = [
            _edge("entrypoint:cs:program", "service:cs:app", "invokes"),
            _edge("service:cs:app", "boundary:cs:http", "exposes"),
            _edge("symbol:cpp:main", "symbol:cpp:driver", "calls"),
            _edge("symbol:cpp:driver", "file:src/driver.hpp", "depends_on"),
        ]

        payload = build_graph_communities({"nodes": nodes, "edges": edges}, top=12)
        languages = {
            language
            for community in payload["communities"]
            for language in community["languages"]
        }

        self.assertIn("csharp", languages)
        self.assertIn("cpp", languages)

    def test_cpp_clusters_follow_topology_not_language_name(self) -> None:
        nodes = {
            "symbol:cpp:main": _node("symbol", "main", language="cpp", file="src/main.cpp"),
            "symbol:cpp:planner": _node("symbol", "Planner", language="cpp", file="src/planner.cpp"),
            "file:src/planner.hpp": _node("file", "planner.hpp", file="src/planner.hpp"),
            "symbol:cpp:test": _node("symbol", "planner_test", language="cpp", file="tests/planner_test.cpp"),
            "symbol:cpp:fake": _node("symbol", "FakePlanner", language="cpp", file="tests/fake.cpp"),
            "file:tests/fake.hpp": _node("file", "fake.hpp", file="tests/fake.hpp"),
        }
        edges = [
            _edge("symbol:cpp:main", "symbol:cpp:planner", "calls"),
            _edge("symbol:cpp:planner", "file:src/planner.hpp", "depends_on", kind="include"),
            _edge("symbol:cpp:test", "symbol:cpp:fake", "calls"),
            _edge("symbol:cpp:fake", "file:tests/fake.hpp", "depends_on", kind="include"),
        ]

        payload = build_graph_communities({"nodes": nodes, "edges": edges}, top=12)

        self.assertNotEqual(
            payload["assignments"]["symbol:cpp:main"],
            payload["assignments"]["symbol:cpp:test"],
        )

    def test_csharp_startup_boundary_and_service_cluster_together(self) -> None:
        nodes = {
            "entrypoint:dotnet:Program": _node("entrypoint", "Program", language="csharp", file="Program.cs"),
            "boundary:dotnet:web": _node("boundary", "ASP.NET boundary", language="csharp"),
            "service:dotnet:orders": _node("service", "OrdersService", language="csharp", file="OrdersService.cs"),
            "config:dotnet:appsettings": _node("config", "appsettings", file="appsettings.json"),
            "symbol:cs:other": _node("symbol", "OtherWorker", language="csharp", file="OtherWorker.cs"),
        }
        edges = [
            _edge("entrypoint:dotnet:Program", "boundary:dotnet:web", "exposes"),
            _edge("entrypoint:dotnet:Program", "service:dotnet:orders", "invokes"),
            _edge("service:dotnet:orders", "config:dotnet:appsettings", "configures"),
            _edge("boundary:dotnet:web", "service:dotnet:orders", "accepts"),
        ]

        payload = build_graph_communities({"nodes": nodes, "edges": edges}, top=12)
        community = _community_by_member(payload, "entrypoint:dotnet:Program")

        self.assertEqual(payload["assignments"]["boundary:dotnet:web"], community["id"])
        self.assertEqual(payload["assignments"]["service:dotnet:orders"], community["id"])
        self.assertEqual(community["dominant_language"], "csharp")


class GraphCommunityProjectionTest(unittest.TestCase):
    """Per ADR 0039: unresolved symbols must not collapse semantic clusters."""

    def _two_islands_glued_by_unresolved(self) -> dict:
        # Two distinct semantic call clusters, with both calling a shared
        # unresolved builtin (the call-graph hub). With the legacy behaviour
        # both clusters merge into one mega-cluster; under the projection
        # contract they stay separate.
        nodes = {
            "symbol:py:left.entry": _node("symbol", "left.entry", language="python", file="left.py"),
            "symbol:py:left.helper": _node("symbol", "left.helper", language="python", file="left.py"),
            "symbol:py:right.entry": _node("symbol", "right.entry", language="python", file="right.py"),
            "symbol:py:right.helper": _node("symbol", "right.helper", language="python", file="right.py"),
            "symbol:unresolved:append": {
                "type": "symbol",
                "label": "append",
                "props": {"resolved": False},
            },
        }
        edges = [
            _edge("symbol:py:left.entry", "symbol:py:left.helper", "calls"),
            _edge("symbol:py:right.entry", "symbol:py:right.helper", "calls"),
            _edge("symbol:py:left.entry", "symbol:unresolved:append", "calls"),
            _edge("symbol:py:left.helper", "symbol:unresolved:append", "calls"),
            _edge("symbol:py:right.entry", "symbol:unresolved:append", "calls"),
            _edge("symbol:py:right.helper", "symbol:unresolved:append", "calls"),
        ]
        return {"nodes": nodes, "edges": edges}

    def test_unresolved_hub_does_not_merge_semantic_islands(self) -> None:
        payload = build_graph_communities(self._two_islands_glued_by_unresolved(), top=12)

        left_cid = payload["assignments"]["symbol:py:left.entry"]
        right_cid = payload["assignments"]["symbol:py:right.entry"]
        self.assertNotEqual(left_cid, right_cid)
        # Members of each island must group with their own entry, not the hub.
        self.assertEqual(payload["assignments"]["symbol:py:left.helper"], left_cid)
        self.assertEqual(payload["assignments"]["symbol:py:right.helper"], right_cid)

    def test_unresolved_symbols_still_appear_in_assignments_as_singletons(self) -> None:
        payload = build_graph_communities(self._two_islands_glued_by_unresolved(), top=12)

        # The unresolved hub is excluded from the projection, but assignments
        # remain complete: every node in the input gets a community id.
        self.assertIn("symbol:unresolved:append", payload["assignments"])
        # Its community must contain only itself (no semantic node grouped in).
        cid = payload["assignments"]["symbol:unresolved:append"]
        siblings = [n for n, c in payload["assignments"].items() if c == cid]
        self.assertEqual(siblings, ["symbol:unresolved:append"])

    def test_payload_exposes_top_level_hubs_field(self) -> None:
        payload = build_graph_communities(self._two_islands_glued_by_unresolved(), top=12)

        self.assertIn("hubs", payload)
        hubs = payload["hubs"]
        self.assertIsInstance(hubs, list)
        self.assertGreaterEqual(len(hubs), 1)
        for entry in hubs:
            self.assertIn("id", entry)
            self.assertIn("label", entry)
            self.assertIn("type", entry)
            self.assertIn("degree", entry)
            self.assertIn("community", entry)
            # Unresolved symbols are call-graph artefacts; they must not
            # appear as hubs.
            self.assertFalse(entry["id"].startswith("symbol:unresolved:"))

    def test_assignments_schema_remains_string_to_community_id(self) -> None:
        # Backwards-compat: the assignments field must keep the {node_id: cid}
        # shape consumers already depend on.
        payload = build_graph_communities(self._two_islands_glued_by_unresolved(), top=12)

        for node_id, cid in payload["assignments"].items():
            self.assertIsInstance(node_id, str)
            self.assertIsInstance(cid, str)
            self.assertTrue(cid.startswith("c"))


if __name__ == "__main__":
    unittest.main()
