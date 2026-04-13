"""Contract tests for the generalized interaction-surface vocabulary.

- project-xoq.1.2: ``rpc``/``channel`` nodes and protocol metadata.
- project-xoq.1.3: explicit validation of interaction metadata and
  boundary semantics, with actionable diagnostics for bundled strategies
  and external adapters.

See ADR 0018 (``docs/adrs/0018-kg-interaction-graph-and-static-truth-policy.md``)
for the rationale, the static-truth policy, and the four protocol
families (HTTP, gRPC, events, ROS2) these additions standardize over.

SCHEMA_VERSION is bumped to 4 for the ``rpc`` and ``channel`` node types
and the optional protocol-metadata vocabulary. No new edge types are
introduced -- existing edges (``exposes``, ``invokes``, ``produces``,
``consumes``, ``implements``) continue to connect interaction surfaces to
the modules and contracts that declare them.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Ensure cortex package is importable from the repo root
_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from cortex.contract import (  # noqa: E402
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

    def test_schema_version_bumped_to_four(self) -> None:
        self.assertEqual(SCHEMA_VERSION, 4)

    def test_interaction_node_types_in_vocabulary(self) -> None:
        for t in _INTERACTION_NODE_TYPES:
            self.assertIn(t, VALID_NODE_TYPES, f"{t!r} missing")

    def test_interaction_node_types_pass_minimal_validation(self) -> None:
        for t in _INTERACTION_NODE_TYPES:
            errs = validate_node(
                f"{t}:demo",
                {"type": t, "label": t, "props": {}},
            )
            self.assertEqual(errs, [], f"{t!r}: {errs}")

    def test_no_new_edge_types_introduced(self) -> None:
        # Phase 7.1 reuses the existing edge vocabulary; no rpc_*/chan_* edges.
        for t in VALID_EDGE_TYPES:
            self.assertFalse(
                t.startswith("rpc_") or t.startswith("chan_"),
                f"unexpected interaction-prefix edge: {t!r}",
            )

    def test_validate_meta_accepts_schema_four(self) -> None:
        self.assertEqual(
            validate_meta({"version": 4, "updated_at": _TS}), []
        )

    def test_validate_meta_rejects_prior_schema_three(self) -> None:
        errs = validate_meta({"version": 3, "updated_at": _TS})
        self.assertTrue(any("version" in e.field for e in errs))

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
            f"NODE_OPTIONAL_PROPS missing: "
            f"{expected - set(NODE_OPTIONAL_PROPS)}",
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
        self.assertTrue(any("protocol" in e.field for e in errs))

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
        self.assertTrue(any("surface_kind" in e.field for e in errs))

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
        self.assertTrue(any("transport" in e.field for e in errs))

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
        self.assertTrue(any("boundary_kind" in e.field for e in errs))

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
        self.assertTrue(any("declared_in" in e.field for e in errs))

    def test_interaction_metadata_omission_is_valid(self) -> None:
        # Per ADR 0018, partial coverage is honest: all new props are optional.
        for t in _INTERACTION_NODE_TYPES:
            errs = validate_node(
                f"{t}:bare",
                {"type": t, "label": t, "props": {}},
            )
            self.assertEqual(errs, [], f"{t!r}: {errs}")

class InteractionMetadataOnExistingNodesTest(unittest.TestCase):
    """Protocol metadata must be accepted on any node type, not just rpc/channel.

    An HTTP route modelled today as ``route`` or a ROS2 topic modelled as
    ``ros_topic`` should be able to carry ``protocol``/``surface_kind``/
    ``transport``/``boundary_kind``/``declared_in`` once extractors stamp
    them. Backward compatibility: existing nodes without these props keep
    validating cleanly.
    """

    def test_route_accepts_http_protocol(self) -> None:
        node = {
            "type": "route",
            "label": "GET /users",
            "props": {
                "protocol": "http",
                "surface_kind": "request_response",
                "transport": "http",
                "boundary_kind": "inbound",
                "declared_in": "services/api/routes.py",
            },
        }
        errs = validate_node("route:get_users", node)
        self.assertEqual(errs, [])

    def test_ros_topic_accepts_ros2_protocol(self) -> None:
        node = {
            "type": "ros_topic",
            "label": "/chatter",
            "props": {
                "protocol": "ros2",
                "surface_kind": "pub_sub",
                "transport": "ros2_dds",
                "boundary_kind": "outbound",
                "declared_in": "src/demo/demo.cpp",
            },
        }
        errs = validate_node("ros_topic:/chatter", node)
        self.assertEqual(errs, [])

    def test_existing_nodes_without_metadata_still_valid(self) -> None:
        # Backward compat: the ``service``/``package`` shapes from the
        # pre-v4 contract must keep validating without any new props.
        for t in ("service", "package", "file", "route", "contract",
                  "ros_topic", "ros_node"):
            errs = validate_node(
                f"{t}:demo",
                {"type": t, "label": t, "props": {}},
            )
            self.assertEqual(errs, [], f"{t!r}: {errs}")

class InteractionGraphIntegrationTest(unittest.TestCase):
    """A tiny graph using rpc/channel nodes + protocol metadata validates."""

    def test_full_graph_with_interaction_surfaces(self) -> None:
        graph = {
            "meta": {"version": SCHEMA_VERSION, "updated_at": _TS},
            "nodes": {
                "service:users": {
                    "type": "service",
                    "label": "users",
                    "props": {},
                },
                "rpc:GetUser": {
                    "type": "rpc",
                    "label": "GetUser",
                    "props": {
                        "protocol": "grpc",
                        "surface_kind": "request_response",
                        "transport": "http2",
                        "boundary_kind": "inbound",
                        "declared_in": "apis/users.proto",
                        "source_strategy": "grpc_proto",
                        "authority": "canonical",
                        "confidence": "definite",
                    },
                },
                "channel:orders.created": {
                    "type": "channel",
                    "label": "orders.created",
                    "props": {
                        "protocol": "event",
                        "surface_kind": "pub_sub",
                        "transport": "kafka",
                        "boundary_kind": "outbound",
                        "declared_in": "schemas/orders_created.avsc",
                        "confidence": "inferred",
                    },
                },
                "contract:users.v1": {
                    "type": "contract",
                    "label": "users.v1",
                    "props": {},
                },
            },
            "edges": [
                {
                    "from": "service:users",
                    "to": "rpc:GetUser",
                    "type": "exposes",
                    "props": {},
                },
                {
                    "from": "service:users",
                    "to": "channel:orders.created",
                    "type": "produces",
                    "props": {},
                },
                {
                    "from": "rpc:GetUser",
                    "to": "contract:users.v1",
                    "type": "implements",
                    "props": {},
                },
            ],
        }
        self.assertEqual(validate_graph(graph), [])

# -- project-xoq.1.3: explicit validation of interaction metadata ----------

class InteractionMetadataTypeCheckTest(unittest.TestCase):
    """project-xoq.1.3: metadata props must be strings before vocabulary check.

    A bundled strategy or external adapter that accidentally emits a
    non-string value (e.g. an int, list, or None) for ``protocol``/
    ``surface_kind``/``transport``/``boundary_kind`` should get an
    explicit "must be a string" diagnostic, not a confusing "invalid
    protocol: 42; valid: [...]" message.
    """

    def _assert_type_error(self, field: str, value: object) -> None:
        node = {
            "type": "rpc",
            "label": "X",
            "props": {field: value},
        }
        errs = validate_node("rpc:x", node)
        matching = [e for e in errs if field in e.field]
        self.assertTrue(
            matching,
            f"{field}={value!r}: expected type error, got {errs}",
        )
        self.assertIn("must be a string", matching[0].message)
        self.assertIn(type(value).__name__, matching[0].message)

    def test_protocol_int_rejected_as_type_error(self) -> None:
        self._assert_type_error("protocol", 42)

    def test_surface_kind_list_rejected_as_type_error(self) -> None:
        self._assert_type_error("surface_kind", ["request_response"])

    def test_transport_none_rejected_as_type_error(self) -> None:
        self._assert_type_error("transport", None)

    def test_boundary_kind_dict_rejected_as_type_error(self) -> None:
        self._assert_type_error("boundary_kind", {"dir": "in"})

    def test_bad_type_does_not_leak_vocabulary_error(self) -> None:
        # When the value is the wrong type, we must not also emit a
        # "invalid protocol: 42; valid: [...]" message; that would be
        # noisy and misleading.
        errs = validate_node(
            "rpc:x",
            {"type": "rpc", "label": "X", "props": {"protocol": 42}},
        )
        for e in errs:
            if "protocol" in e.field:
                self.assertNotIn("valid:", e.message)

class InteractionMetadataEmptyStringTest(unittest.TestCase):
    """project-xoq.1.3: empty strings are guessing, not silence.

    Per ADR 0018, omission is preferred over guessing. Empty string
    props are neither: they pretend to declare a boundary without
    supplying a value. Reject them loudly for ``declared_in``,
    ``protocol``, ``surface_kind``, ``transport``, and ``boundary_kind``.
    """

    def _assert_empty_rejected(self, field: str) -> None:
        node = {
            "type": "rpc",
            "label": "X",
            "props": {field: ""},
        }
        errs = validate_node("rpc:x", node)
        matching = [e for e in errs if field in e.field]
        self.assertTrue(matching, f"{field}='': expected error, got {errs}")
        self.assertIn("empty", matching[0].message.lower())

    def test_declared_in_empty_rejected(self) -> None:
        self._assert_empty_rejected("declared_in")

    def test_protocol_empty_rejected(self) -> None:
        self._assert_empty_rejected("protocol")

    def test_transport_empty_rejected(self) -> None:
        self._assert_empty_rejected("transport")

    def test_boundary_kind_empty_rejected(self) -> None:
        self._assert_empty_rejected("boundary_kind")

    def test_surface_kind_empty_rejected(self) -> None:
        self._assert_empty_rejected("surface_kind")

class InteractionProtocolTransportCoherenceTest(unittest.TestCase):
    """project-xoq.1.3: protocol + transport pairs must be coherent.

    When a strategy stamps both ``protocol`` and ``transport`` on the
    same node, the pair must be physically plausible per ADR 0018's
    static-truth policy. A ROS2 surface does not ride on Kafka; an
    event bus does not ride on ROS2 DDS. Rather than silently carry
    such claims, fail loudly so the strategy either drops the
    guessed prop or fixes the mapping.

    Omission is still fine: nodes without ``transport`` validate
    without any cross-prop check.
    """

    def test_http_on_http_transport_valid(self) -> None:
        node = {
            "type": "route",
            "label": "GET /users",
            "props": {"protocol": "http", "transport": "http"},
        }
        self.assertEqual(validate_node("route:x", node), [])

    def test_http_on_http2_transport_valid(self) -> None:
        node = {
            "type": "route",
            "label": "GET /users",
            "props": {"protocol": "http", "transport": "http2"},
        }
        self.assertEqual(validate_node("route:x", node), [])

    def test_grpc_on_http2_transport_valid(self) -> None:
        node = {
            "type": "rpc",
            "label": "GetUser",
            "props": {"protocol": "grpc", "transport": "http2"},
        }
        self.assertEqual(validate_node("rpc:x", node), [])

    def test_ros2_on_dds_transport_valid(self) -> None:
        node = {
            "type": "ros_topic",
            "label": "/chatter",
            "props": {"protocol": "ros2", "transport": "ros2_dds"},
        }
        self.assertEqual(validate_node("ros_topic:x", node), [])

    def test_event_on_kafka_valid(self) -> None:
        node = {
            "type": "channel",
            "label": "orders",
            "props": {"protocol": "event", "transport": "kafka"},
        }
        self.assertEqual(validate_node("channel:x", node), [])

    def test_ros2_on_kafka_rejected(self) -> None:
        node = {
            "type": "ros_topic",
            "label": "/chatter",
            "props": {"protocol": "ros2", "transport": "kafka"},
        }
        errs = validate_node("ros_topic:x", node)
        self.assertTrue(
            any("transport" in e.field and "ros2" in e.message for e in errs),
            errs,
        )

    def test_event_on_ros2_dds_rejected(self) -> None:
        node = {
            "type": "channel",
            "label": "orders",
            "props": {"protocol": "event", "transport": "ros2_dds"},
        }
        errs = validate_node("channel:x", node)
        self.assertTrue(
            any("transport" in e.field for e in errs),
            errs,
        )

    def test_http_on_ros2_dds_rejected(self) -> None:
        node = {
            "type": "route",
            "label": "GET /x",
            "props": {"protocol": "http", "transport": "ros2_dds"},
        }
        errs = validate_node("route:x", node)
        self.assertTrue(
            any("transport" in e.field for e in errs),
            errs,
        )

    def test_protocol_without_transport_valid(self) -> None:
        # Partial coverage is honest: no transport stamped is fine.
        node = {
            "type": "rpc",
            "label": "GetUser",
            "props": {"protocol": "grpc"},
        }
        self.assertEqual(validate_node("rpc:x", node), [])

    def test_transport_without_protocol_valid(self) -> None:
        # Unknown protocol but known transport is also honest; the
        # coherence check only fires when both are present.
        node = {
            "type": "channel",
            "label": "x",
            "props": {"transport": "kafka"},
        }
        self.assertEqual(validate_node("channel:x", node), [])

class InteractionDiagnosticsSourceLabelTest(unittest.TestCase):
    """project-xoq.1.3: bundled strategies and adapters see source_label.

    When a fragment from ``strategy:grpc_proto`` or ``adapter:custom``
    emits a bad interaction prop, the resulting diagnostic must carry
    the source label so the operator can tell *which* producer is
    misbehaving. Before project-xoq.1.3, ``validate_node`` hard-coded
    the path as ``nodes.<id>`` and the source label was only attached
    to top-level structural errors.
    """

    def test_strategy_source_label_in_node_diagnostic(self) -> None:
        frag = {
            "nodes": {
                "rpc:bad": {
                    "type": "rpc",
                    "label": "Bad",
                    "props": {"protocol": "carrier-pigeon"},
                },
            },
            "edges": [],
        }
        errs = validate_fragment(frag, source_label="strategy:grpc_proto")
        matching = [e for e in errs if "protocol" in e.field]
        self.assertTrue(matching, errs)
        self.assertTrue(
            all("strategy:grpc_proto" in e.path for e in matching),
            [e.path for e in matching],
        )

    def test_adapter_source_label_in_incoherent_transport_diagnostic(self) -> None:
        frag = {
            "nodes": {
                "channel:x": {
                    "type": "channel",
                    "label": "x",
                    "props": {"protocol": "ros2", "transport": "kafka"},
                },
            },
            "edges": [],
        }
        errs = validate_fragment(frag, source_label="adapter:custom")
        matching = [e for e in errs if "transport" in e.field]
        self.assertTrue(matching, errs)
        self.assertTrue(
            all("adapter:custom" in e.path for e in matching),
            [e.path for e in matching],
        )

    def test_adapter_source_label_in_type_error_diagnostic(self) -> None:
        frag = {
            "nodes": {
                "rpc:x": {
                    "type": "rpc",
                    "label": "X",
                    "props": {"protocol": 42},
                },
            },
            "edges": [],
        }
        errs = validate_fragment(frag, source_label="adapter:external_json")
        matching = [e for e in errs if "protocol" in e.field]
        self.assertTrue(matching, errs)
        self.assertTrue(
            all("adapter:external_json" in e.path for e in matching),
            [e.path for e in matching],
        )

    def test_default_source_label_does_not_leak_for_valid_fragment(self) -> None:
        # Sanity: a clean fragment produces no errors regardless of label.
        frag = {
            "nodes": {
                "rpc:ok": {
                    "type": "rpc",
                    "label": "OK",
                    "props": {
                        "protocol": "grpc",
                        "transport": "http2",
                        "surface_kind": "request_response",
                        "boundary_kind": "inbound",
                        "declared_in": "apis/users.proto",
                    },
                },
            },
            "edges": [],
        }
        self.assertEqual(
            validate_fragment(frag, source_label="strategy:grpc_proto"), [],
        )

if __name__ == "__main__":
    unittest.main()
