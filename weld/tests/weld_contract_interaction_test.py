"""Contract tests for the generalized interaction-surface vocabulary.

- project-xoq.1.2: ``rpc``/``channel`` nodes and protocol metadata.
- project-xoq.1.3: explicit validation of interaction metadata and
  boundary semantics, with actionable diagnostics for bundled strategies
  and external adapters.

See ADR 0018 (the interaction-structure and static-truth ADR) for the
rationale, the static-truth policy, and the four protocol families
(HTTP, gRPC, events, ROS2) these additions standardize over.

SCHEMA_VERSION was bumped to 4 for the ``rpc`` and ``channel`` node types
and the optional protocol-metadata vocabulary. No new edge types are
introduced -- existing edges (``exposes``, ``invokes``, ``produces``,
``consumes``, ``implements``) continue to connect interaction surfaces to
the modules and contracts that declare them.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Ensure weld package is importable from the repo root
_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.contract import (  # noqa: E402
    BOUNDARY_KIND_VALUES,
    NODE_OPTIONAL_PROPS,
    PROTOCOL_VALUES,
    SCHEMA_VERSION,
    SURFACE_KIND_VALUES,
    TRANSPORT_VALUES,
    VALID_EDGE_TYPES,
    VALID_NODE_TYPES,
    validate_fragment,
    validate_graph,
    validate_meta,
    validate_node,
)

_TS = "2026-04-09T12:00:00+00:00"

_INTERACTION_NODE_TYPES = ["rpc", "channel"]

_INTERACTION_OPTIONAL_PROPS = [
    "protocol",
    "surface_kind",
    "transport",
    "boundary_kind",
    "declared_in",
]


class InteractionSchemaTest(unittest.TestCase):
    """project-xoq.1.2: rpc/channel vocabulary and protocol metadata."""

    def test_schema_version_at_least_four(self) -> None:
        self.assertGreaterEqual(SCHEMA_VERSION, 4)

    def test_interaction_node_types_in_vocabulary(self) -> None:
        for node_type in _INTERACTION_NODE_TYPES:
            self.assertIn(node_type, VALID_NODE_TYPES, f"{node_type!r} missing")

    def test_interaction_node_types_pass_minimal_validation(self) -> None:
        for node_type in _INTERACTION_NODE_TYPES:
            errs = validate_node(
                f"{node_type}:demo",
                {"type": node_type, "label": node_type, "props": {}},
            )
            self.assertEqual(errs, [], f"{node_type!r}: {errs}")

    def test_no_new_edge_types_introduced(self) -> None:
        for edge_type in VALID_EDGE_TYPES:
            self.assertFalse(
                edge_type.startswith("rpc_") or edge_type.startswith("chan_"),
                f"unexpected interaction-prefix edge: {edge_type!r}",
            )

    def test_validate_meta_accepts_current_schema(self) -> None:
        self.assertEqual(validate_meta({"version": SCHEMA_VERSION, "updated_at": _TS}), [])

    def test_validate_meta_rejects_prior_schema_three(self) -> None:
        errs = validate_meta({"version": 3, "updated_at": _TS})
        self.assertTrue(any("version" in err.field for err in errs))


class ProtocolMetadataVocabularyTest(unittest.TestCase):
    """Each new optional prop has a closed vocabulary; omissions are fine."""

    def test_protocol_vocabulary_non_empty(self) -> None:
        self.assertTrue(len(PROTOCOL_VALUES) > 0)
        for expected in ("http", "grpc", "event", "ros2"):
            self.assertIn(expected, PROTOCOL_VALUES)

    def test_surface_kind_vocabulary_non_empty(self) -> None:
        self.assertTrue(len(SURFACE_KIND_VALUES) > 0)
        for expected in ("request_response", "pub_sub", "stream", "one_way"):
            self.assertIn(expected, SURFACE_KIND_VALUES)

    def test_transport_vocabulary_non_empty(self) -> None:
        self.assertTrue(len(TRANSPORT_VALUES) > 0)
        for expected in ("http", "http2", "ros2_dds"):
            self.assertIn(expected, TRANSPORT_VALUES)

    def test_boundary_kind_vocabulary_non_empty(self) -> None:
        self.assertTrue(len(BOUNDARY_KIND_VALUES) > 0)
        for expected in ("inbound", "outbound", "internal"):
            self.assertIn(expected, BOUNDARY_KIND_VALUES)

    def test_interaction_optional_props_documented(self) -> None:
        expected = set(_INTERACTION_OPTIONAL_PROPS)
        self.assertTrue(
            expected.issubset(set(NODE_OPTIONAL_PROPS)),
            f"NODE_OPTIONAL_PROPS missing: {expected - set(NODE_OPTIONAL_PROPS)}",
        )

    def test_valid_protocol_values(self) -> None:
        for value in PROTOCOL_VALUES:
            node = {
                "type": "rpc",
                "label": "GetUser",
                "props": {"protocol": value},
            }
            errs = validate_node("rpc:GetUser", node)
            self.assertEqual(errs, [], f"protocol={value!r}: {errs}")

    def test_invalid_protocol_rejected(self) -> None:
        node = {
            "type": "rpc",
            "label": "GetUser",
            "props": {"protocol": "carrier-pigeon"},
        }
        errs = validate_node("rpc:GetUser", node)
        self.assertTrue(any("protocol" in err.field for err in errs))

    def test_valid_surface_kind_values(self) -> None:
        for value in SURFACE_KIND_VALUES:
            node = {
                "type": "rpc",
                "label": "GetUser",
                "props": {"surface_kind": value},
            }
            errs = validate_node("rpc:GetUser", node)
            self.assertEqual(errs, [], f"surface_kind={value!r}: {errs}")

    def test_invalid_surface_kind_rejected(self) -> None:
        node = {
            "type": "rpc",
            "label": "GetUser",
            "props": {"surface_kind": "telepathy"},
        }
        errs = validate_node("rpc:GetUser", node)
        self.assertTrue(any("surface_kind" in err.field for err in errs))

    def test_valid_transport_values(self) -> None:
        for value in TRANSPORT_VALUES:
            node = {
                "type": "channel",
                "label": "orders",
                "props": {"transport": value},
            }
            errs = validate_node("channel:orders", node)
            self.assertEqual(errs, [], f"transport={value!r}: {errs}")

    def test_invalid_transport_rejected(self) -> None:
        node = {
            "type": "channel",
            "label": "orders",
            "props": {"transport": "tin-can-string"},
        }
        errs = validate_node("channel:orders", node)
        self.assertTrue(any("transport" in err.field for err in errs))

    def test_valid_boundary_kind_values(self) -> None:
        for value in BOUNDARY_KIND_VALUES:
            node = {
                "type": "rpc",
                "label": "GetUser",
                "props": {"boundary_kind": value},
            }
            errs = validate_node("rpc:GetUser", node)
            self.assertEqual(errs, [], f"boundary_kind={value!r}: {errs}")

    def test_invalid_boundary_kind_rejected(self) -> None:
        node = {
            "type": "rpc",
            "label": "GetUser",
            "props": {"boundary_kind": "sideways"},
        }
        errs = validate_node("rpc:GetUser", node)
        self.assertTrue(any("boundary_kind" in err.field for err in errs))

    def test_valid_declared_in_string(self) -> None:
        node = {
            "type": "rpc",
            "label": "GetUser",
            "props": {"declared_in": "apis/users.proto"},
        }
        errs = validate_node("rpc:GetUser", node)
        self.assertEqual(errs, [])

    def test_declared_in_must_be_string(self) -> None:
        node = {
            "type": "rpc",
            "label": "GetUser",
            "props": {"declared_in": 42},
        }
        errs = validate_node("rpc:GetUser", node)
        self.assertTrue(any("declared_in" in err.field for err in errs))

    def test_interaction_metadata_omission_is_valid(self) -> None:
        for node_type in _INTERACTION_NODE_TYPES:
            errs = validate_node(
                f"{node_type}:demo",
                {"type": node_type, "label": node_type, "props": {}},
            )
            self.assertEqual(errs, [], f"{node_type!r}: {errs}")


class InteractionGraphValidationTest(unittest.TestCase):
    """Fragment- and graph-level validation for interaction surfaces."""

    def test_rpc_to_contract_fragment_is_valid(self) -> None:
        fragment = {
            "nodes": {
                "rpc:GetUser": {
                    "type": "rpc",
                    "label": "GetUser",
                    "props": {
                        "protocol": "grpc",
                        "surface_kind": "request_response",
                        "transport": "http2",
                    },
                },
                "contract:users.proto:GetUser": {
                    "type": "contract",
                    "label": "GetUser",
                    "props": {},
                },
            },
            "edges": [
                {
                    "from": "rpc:GetUser",
                    "to": "contract:users.proto:GetUser",
                    "type": "implements",
                    "props": {},
                }
            ],
        }
        self.assertEqual(validate_fragment(fragment), [])

    def test_channel_to_contract_fragment_is_valid(self) -> None:
        fragment = {
            "nodes": {
                "channel:orders": {
                    "type": "channel",
                    "label": "orders",
                    "props": {
                        "protocol": "event",
                        "surface_kind": "pub_sub",
                    },
                },
                "contract:events/order_created": {
                    "type": "contract",
                    "label": "order_created",
                    "props": {},
                },
            },
            "edges": [
                {
                    "from": "channel:orders",
                    "to": "contract:events/order_created",
                    "type": "implements",
                    "props": {},
                }
            ],
        }
        self.assertEqual(validate_fragment(fragment), [])

    def test_full_graph_with_interaction_surfaces_is_valid(self) -> None:
        graph = {
            "meta": {"version": SCHEMA_VERSION, "updated_at": _TS},
            "nodes": {
                "boundary:api": {"type": "boundary", "label": "API", "props": {}},
                "rpc:GetUser": {
                    "type": "rpc",
                    "label": "GetUser",
                    "props": {"protocol": "grpc", "boundary_kind": "inbound"},
                },
                "channel:orders": {
                    "type": "channel",
                    "label": "orders",
                    "props": {"protocol": "event", "boundary_kind": "outbound"},
                },
            },
            "edges": [
                {
                    "from": "boundary:api",
                    "to": "rpc:GetUser",
                    "type": "exposes",
                    "props": {},
                },
                {
                    "from": "channel:orders",
                    "to": "boundary:api",
                    "type": "depends_on",
                    "props": {},
                },
            ],
        }
        self.assertEqual(validate_graph(graph), [])


if __name__ == "__main__":
    unittest.main()
