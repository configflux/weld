"""v2 tests for wd brief -- interfaces bucket and interaction-aware ranking.

Split from ``weld_brief_test.py`` to keep that file under the 400-line lint
cap while v1 coverage stays intact. The v1 classification/ranking/limit
tests remain in ``weld_brief_test.py``; this module owns the contract that
came from bd ``tracked project``:

  - ``brief_version`` is 2
  - ``interfaces`` bucket exists and classifies rpc/channel/ROS2 surfaces
  - Nodes that carry static interaction-surface metadata are promoted
    into interfaces even when their primary type is ``route`` or similar
  - Queries that mention interaction concepts (protocol, endpoint, rpc,
    channel, ...) emit interfaces + boundaries before primary in the
    envelope field order and mark the boost in ``relevance``

"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.brief import _classify_node, brief  # noqa: E402
from weld.contract import SCHEMA_VERSION  # noqa: E402
from weld.graph import Graph  # noqa: E402

_TS = "2026-04-02T12:00:00+00:00"

def _make_graph(nodes: dict, edges: list | None = None) -> Graph:
    """Create a Graph instance pre-loaded with in-memory data."""
    tmpdir = Path(tempfile.mkdtemp())
    g = Graph(tmpdir)
    g.load()
    g._data = {
        "meta": {"version": SCHEMA_VERSION, "updated_at": _TS, "git_sha": "v2t"},
        "nodes": nodes,
        "edges": edges or [],
    }
    return g

class BriefV2ClassificationTest(unittest.TestCase):
    """Classification of interaction-surface node types into interfaces."""

    def test_rpc_node_classified_as_interface(self) -> None:
        node = {"id": "rpc:get-user", "type": "rpc", "label": "GetUser",
                "props": {"protocol": "grpc"}}
        self.assertEqual(_classify_node(node), "interface")

    def test_channel_node_classified_as_interface(self) -> None:
        node = {"id": "channel:events", "type": "channel", "label": "events",
                "props": {"protocol": "event"}}
        self.assertEqual(_classify_node(node), "interface")

    def test_ros_service_classified_as_interface(self) -> None:
        node = {"id": "ros_service:add_two_ints", "type": "ros_service",
                "label": "add_two_ints", "props": {}}
        self.assertEqual(_classify_node(node), "interface")

    def test_ros_topic_classified_as_interface(self) -> None:
        node = {"id": "ros_topic:/cmd_vel", "type": "ros_topic",
                "label": "/cmd_vel", "props": {}}
        self.assertEqual(_classify_node(node), "interface")

    def test_route_with_protocol_promoted_to_interface(self) -> None:
        node = {"id": "route:GET-/users", "type": "route",
                "label": "GET /users", "props": {"protocol": "http"}}
        self.assertEqual(_classify_node(node), "interface")

    def test_boundary_with_protocol_stays_boundary(self) -> None:
        node = {"id": "boundary:edge", "type": "boundary", "label": "edge",
                "props": {"protocol": "http"}}
        self.assertEqual(_classify_node(node), "boundary")

    def test_doc_with_protocol_stays_doc(self) -> None:
        node = {"id": "doc:api", "type": "doc", "label": "API doc",
                "props": {"protocol": "http"}}
        self.assertEqual(_classify_node(node), "doc")

    def test_build_target_with_protocol_stays_build(self) -> None:
        node = {"id": "build-target:api", "type": "build-target",
                "label": "//api", "props": {"protocol": "http"}}
        self.assertEqual(_classify_node(node), "build")

class BriefInterfacesBucketTest(unittest.TestCase):
    """Interfaces bucket surfaces rpc/channel/protocol-annotated nodes."""

    def _graph_with_interfaces(self) -> Graph:
        nodes = {
            "service:orders": {
                "type": "service", "label": "orders api",
                "props": {"authority": "canonical", "confidence": "definite",
                          "file": "services/orders/main.py"},
            },
            "rpc:create-order": {
                "type": "rpc", "label": "create_order api rpc",
                "props": {"authority": "canonical", "confidence": "definite",
                          "protocol": "grpc", "surface_kind": "request_response",
                          "boundary_kind": "inbound"},
            },
            "channel:order-events": {
                "type": "channel", "label": "order events api",
                "props": {"authority": "derived", "confidence": "inferred",
                          "protocol": "event", "surface_kind": "pub_sub",
                          "boundary_kind": "outbound"},
            },
            "boundary:public-api": {
                "type": "boundary", "label": "public api boundary",
                "props": {"authority": "canonical", "confidence": "definite",
                          "protocol": "http", "boundary_kind": "inbound"},
            },
        }
        return _make_graph(nodes)

    def test_rpc_appears_in_interfaces_bucket(self) -> None:
        g = self._graph_with_interfaces()
        result = brief(g, "api")
        iface_ids = {n["id"] for n in result["interfaces"]}
        self.assertIn("rpc:create-order", iface_ids)

    def test_channel_appears_in_interfaces_bucket(self) -> None:
        g = self._graph_with_interfaces()
        result = brief(g, "api")
        iface_ids = {n["id"] for n in result["interfaces"]}
        self.assertIn("channel:order-events", iface_ids)

    def test_boundary_still_in_boundaries_not_interfaces(self) -> None:
        g = self._graph_with_interfaces()
        result = brief(g, "api")
        iface_ids = {n["id"] for n in result["interfaces"]}
        bnd_ids = {n["id"] for n in result["boundaries"]}
        self.assertNotIn("boundary:public-api", iface_ids)
        self.assertIn("boundary:public-api", bnd_ids)

    def test_interfaces_bucket_is_always_present(self) -> None:
        g = _make_graph({})
        result = brief(g, "anything")
        self.assertIn("interfaces", result)
        self.assertEqual(result["interfaces"], [])

    def test_interface_nodes_have_relevance_field(self) -> None:
        g = self._graph_with_interfaces()
        result = brief(g, "api")
        for node in result["interfaces"]:
            self.assertIn("relevance", node)

class BriefInteractionRankingTest(unittest.TestCase):
    """Interaction-relevant queries put interfaces/boundaries first."""

    def _mixed_graph(self) -> Graph:
        nodes = {
            "service:payments": {
                "type": "service", "label": "payments service",
                "props": {"authority": "canonical", "confidence": "definite",
                          "file": "services/payments/main.py"},
            },
            "rpc:charge": {
                "type": "rpc", "label": "payments charge endpoint",
                "props": {"authority": "canonical", "confidence": "definite",
                          "protocol": "grpc", "surface_kind": "request_response",
                          "boundary_kind": "inbound"},
            },
        }
        return _make_graph(nodes)

    def test_interaction_query_emits_interfaces_before_primary(self) -> None:
        g = self._mixed_graph()
        result = brief(g, "payments endpoint")
        keys = list(result.keys())
        self.assertLess(
            keys.index("interfaces"), keys.index("primary"),
            f"interfaces should precede primary in envelope: {keys}",
        )
        self.assertLess(
            keys.index("boundaries"), keys.index("primary"),
            f"boundaries should precede primary in envelope: {keys}",
        )

    def test_non_interaction_query_keeps_primary_first(self) -> None:
        g = self._mixed_graph()
        result = brief(g, "payments service")
        keys = list(result.keys())
        self.assertLess(
            keys.index("primary"), keys.index("interfaces"),
            f"primary should precede interfaces for generic query: {keys}",
        )

    def test_route_with_protocol_promoted_into_interfaces(self) -> None:
        # A ``route`` node with protocol metadata is promoted into
        # interfaces rather than sitting in primary.
        nodes = {
            "route:charge": {
                "type": "route", "label": "charge http route",
                "props": {"authority": "canonical", "confidence": "definite",
                          "protocol": "http"},
            },
            "service:charge_svc": {
                "type": "service", "label": "charge service",
                "props": {"authority": "canonical", "confidence": "definite"},
            },
        }
        g = _make_graph(nodes)
        result = brief(g, "charge endpoint")
        iface_ids = {n["id"] for n in result["interfaces"]}
        prim_ids = {n["id"] for n in result["primary"]}
        self.assertIn("route:charge", iface_ids)
        self.assertNotIn("route:charge", prim_ids)

    def test_interaction_query_marks_boost_in_relevance(self) -> None:
        g = self._mixed_graph()
        result = brief(g, "payments endpoint")
        matched = [n for n in result["interfaces"] if n["id"] == "rpc:charge"]
        self.assertEqual(len(matched), 1)
        self.assertIn("interaction_boost", matched[0]["relevance"])

    def test_non_interaction_query_skips_boost_marker(self) -> None:
        g = self._mixed_graph()
        result = brief(g, "payments service")
        for node in result["interfaces"]:
            self.assertNotIn("interaction_boost", node["relevance"])

    def test_brief_version_is_2(self) -> None:
        g = _make_graph({})
        result = brief(g, "anything")
        self.assertEqual(result["brief_version"], 2)

if __name__ == "__main__":
    unittest.main()
