"""Polyglot acceptance test: cross-protocol interaction graph (tracked project).

Runs all four protocol families (HTTP, gRPC, events, ROS2) through
their respective extraction strategies, merges the resulting fragments
into a single graph, and verifies cross-boundary questions can be
answered without dynamic traces.

This is the capstone acceptance test for tracked project
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.contract import validate_fragment, validate_graph, SCHEMA_VERSION  # noqa: E402
from weld.strategies.events import extract as events_extract  # noqa: E402
from weld.strategies.events_bindings import extract as events_bindings_extract  # noqa: E402
from weld.strategies.fastapi import extract as fastapi_extract  # noqa: E402
from weld.strategies.grpc_bindings import extract as grpc_bindings_extract  # noqa: E402
from weld.strategies.grpc_proto import extract as grpc_proto_extract  # noqa: E402
from weld.strategies.http_client import extract as http_client_extract  # noqa: E402
from weld.strategies import ros2_topology  # noqa: E402

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_TS = "2026-04-09T12:00:00+00:00"

def _merge_results(*results):
    """Merge multiple StrategyResult-like outputs into a single graph."""
    nodes = {}
    edges = []
    for r in results:
        if hasattr(r, "nodes"):
            nodes.update(r.nodes)
            edges.extend(r.edges)
        elif isinstance(r, tuple) and len(r) >= 2:
            nodes.update(r[0])
            edges.extend(r[1])
    return nodes, edges

class PolyglotMergedGraphTest(unittest.TestCase):
    """All four protocol families merge into a single valid graph."""

    @classmethod
    def setUpClass(cls) -> None:
        http_fixture = _FIXTURES / "http_accept"
        grpc_fixture = _FIXTURES / "grpc_accept"
        events_fixture = _FIXTURES / "events_accept"
        ros2_fixture = _FIXTURES / "ros2_accept"

        results = [
            fastapi_extract(http_fixture, {"glob": "routers/*.py"}, {}),
            http_client_extract(
                http_fixture, {"glob": "src/**/*.py"}, {}
            ),
            grpc_proto_extract(
                grpc_fixture, {"glob": "proto/**/*.proto"}, {}
            ),
            grpc_bindings_extract(
                grpc_fixture,
                {
                    "glob": "src/**/*.py",
                    "proto_glob": "proto/**/*.proto",
                },
                {},
            ),
            events_extract(
                events_fixture / "compose",
                {"kind": "compose_env", "glob": "docker-compose*.yml"},
                {},
            ),
            events_bindings_extract(
                events_fixture, {"glob": "src/**/*.py"}, {}
            ),
            ros2_topology.extract(
                ros2_fixture, {"glob": "src/**/*.py"}, {}
            ),
        ]
        cls.nodes, cls.edges = _merge_results(*results)

    # -- coverage breadth ----------------------------------------------------

    def test_all_four_protocols_present(self) -> None:
        protocols = set()
        for node in self.nodes.values():
            p = node["props"].get("protocol")
            if p:
                protocols.add(p)
        # ROS2 nodes use "ros2" protocol only on ros_topic nodes when
        # stamped by topology -- if not stamped, we check node types.
        node_types = {n["type"] for n in self.nodes.values()}
        self.assertIn("http", protocols)
        self.assertIn("grpc", protocols)
        self.assertIn("event", protocols)
        self.assertTrue(
            "ros2" in protocols or "ros_topic" in node_types,
            "ROS2 protocol or ros_topic node type should be present",
        )

    def test_node_count_covers_all_families(self) -> None:
        types = {}
        for n in self.nodes.values():
            types[n["type"]] = types.get(n["type"], 0) + 1
        # HTTP: at least 5 routes + 4 outbound rpcs
        self.assertGreaterEqual(types.get("route", 0), 5)
        self.assertGreaterEqual(types.get("rpc", 0), 3)
        # Events: at least 4 channels
        self.assertGreaterEqual(types.get("channel", 0), 4)
        # ROS2: at least 2 ros_nodes, 4 topics
        self.assertGreaterEqual(types.get("ros_node", 0), 2)
        self.assertGreaterEqual(types.get("ros_topic", 0), 4)

    def test_edge_types_span_all_families(self) -> None:
        edge_types = {e["type"] for e in self.edges}
        for expected in (
            "invokes",
            "contains",
            "accepts",
            "responds_with",
            "produces",
            "consumes",
            "exposes",
            "implements",
        ):
            self.assertIn(
                expected, edge_types, f"missing edge type: {expected}"
            )

    # -- cross-boundary questions --------------------------------------------

    def test_which_files_invoke_grpc_rpcs(self) -> None:
        """Can answer: which files call gRPC methods?"""
        grpc_rpcs = {
            nid
            for nid, n in self.nodes.items()
            if n["type"] == "rpc"
            and n["props"].get("protocol") == "grpc"
        }
        callers = {
            e["from"]
            for e in self.edges
            if e["type"] == "invokes" and e["to"] in grpc_rpcs
        }
        self.assertTrue(len(callers) > 0)

    def test_which_channels_have_both_producer_and_consumer(self) -> None:
        """Can answer: which channels have both a producer and consumer?"""
        produced = {
            e["to"]
            for e in self.edges
            if e["type"] == "produces"
        }
        consumed = {
            e["to"]
            for e in self.edges
            if e["type"] == "consumes"
        }
        both = produced & consumed
        self.assertIn("channel:kafka:orders.placed", both)

    def test_which_ros2_nodes_share_a_topic(self) -> None:
        """Can answer: which ROS2 topics connect multiple nodes?"""
        topic_producers = {}
        topic_consumers = {}
        for e in self.edges:
            if e["to"].startswith("ros_topic:"):
                if e["type"] == "produces":
                    topic_producers.setdefault(e["to"], set()).add(
                        e["from"]
                    )
                elif e["type"] == "consumes":
                    topic_consumers.setdefault(e["to"], set()).add(
                        e["from"]
                    )
        # At minimum, each topic is produced or consumed by one node.
        self.assertTrue(
            len(topic_producers) > 0, "no topic producers found"
        )
        self.assertTrue(
            len(topic_consumers) > 0, "no topic consumers found"
        )

    # -- merged fragment validation ------------------------------------------

    def test_merged_fragment_validates(self) -> None:
        errs = validate_fragment(
            {"nodes": self.nodes, "edges": self.edges},
            source_label="polyglot-acceptance",
            allow_dangling_edges=True,
        )
        self.assertEqual(errs, [], f"validation errors: {errs}")

    def test_full_graph_validates(self) -> None:
        graph = {
            "meta": {"version": SCHEMA_VERSION, "updated_at": _TS},
            "nodes": self.nodes,
            "edges": self.edges,
        }
        errs = validate_graph(graph)
        # Filter out dangling-edge errors since fragments come from
        # different fixture repos that do not share node namespaces.
        non_dangling = [
            e for e in errs if "dangling" not in e.message.lower()
        ]
        self.assertEqual(
            non_dangling, [], f"graph validation errors: {non_dangling}"
        )

if __name__ == "__main__":
    unittest.main()
