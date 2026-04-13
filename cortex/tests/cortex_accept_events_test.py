"""Acceptance tests for event/channel interaction graph extraction (project-xoq.7.2).

Exercises the events (channel declarations from compose env) and
events_bindings (producer/consumer linking) strategies against the
``events_accept`` fixture. Verifies channel nodes, protocol metadata,
and producer/consumer edges.

Per ADR 0018, compose-declared channels are canonical
(confidence=definite) while binding edges are inferred.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from cortex.contract import validate_fragment  # noqa: E402
from cortex.strategies.events import extract as events_extract  # noqa: E402
from cortex.strategies.events_bindings import extract as bindings_extract  # noqa: E402

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "events_accept"

class EventChannelDeclarationAcceptanceTest(unittest.TestCase):
    """Compose env vars produce channel nodes with protocol metadata."""

    def setUp(self) -> None:
        result = events_extract(
            _FIXTURE / "compose",
            {"kind": "compose_env", "glob": "docker-compose*.yml"},
            {},
        )
        self.nodes = result.nodes
        self.edges = result.edges

    def test_kafka_channels_extracted(self) -> None:
        by_name = {
            n["props"]["name"]: n
            for n in self.nodes.values()
            if n["type"] == "channel"
        }
        self.assertIn("orders.placed", by_name)
        self.assertIn("shipments.dispatched", by_name)
        self.assertIn("notifications.email", by_name)

    def test_redis_channel_extracted(self) -> None:
        by_name = {
            n["props"]["name"]: n
            for n in self.nodes.values()
            if n["type"] == "channel"
        }
        self.assertIn("alerts:critical", by_name)
        node = by_name["alerts:critical"]
        self.assertEqual(node["props"]["transport"], "tcp")

    def test_kafka_channel_protocol_metadata(self) -> None:
        kafka_channels = [
            n
            for n in self.nodes.values()
            if n["type"] == "channel"
            and n["props"].get("transport") == "kafka"
        ]
        self.assertGreaterEqual(len(kafka_channels), 3)
        for node in kafka_channels:
            props = node["props"]
            self.assertEqual(props["protocol"], "event")
            self.assertEqual(props["surface_kind"], "pub_sub")
            self.assertEqual(props["boundary_kind"], "internal")
            self.assertEqual(props["confidence"], "definite")

    def test_non_channel_env_vars_ignored(self) -> None:
        """LOG_LEVEL and DB_HOST should not produce channel nodes."""
        all_names = {
            n["props"].get("name", "")
            for n in self.nodes.values()
        }
        self.assertNotIn("INFO", all_names)
        self.assertNotIn("db", all_names)

    def test_contains_edges_link_file_to_channels(self) -> None:
        contains = [e for e in self.edges if e["type"] == "contains"]
        self.assertGreaterEqual(len(contains), 4)
        targets = {e["to"] for e in contains}
        self.assertIn("channel:kafka:orders.placed", targets)

    def test_declaration_fragment_validates(self) -> None:
        errs = validate_fragment(
            {"nodes": self.nodes, "edges": self.edges},
            source_label="strategy:events",
            allow_dangling_edges=True,
        )
        self.assertEqual(errs, [], f"validation errors: {errs}")

class EventBindingsAcceptanceTest(unittest.TestCase):
    """Producer and consumer call sites produce correct edges."""

    def setUp(self) -> None:
        result = bindings_extract(
            _FIXTURE, {"glob": "src/**/*.py"}, {}
        )
        self.edges = result.edges

    def test_producer_edges_emitted(self) -> None:
        produces = [e for e in self.edges if e["type"] == "produces"]
        targets = {e["to"] for e in produces}
        self.assertIn("channel:kafka:orders.placed", targets)
        self.assertIn("channel:tcp:alerts:critical", targets)

    def test_consumer_edges_emitted(self) -> None:
        consumes = [e for e in self.edges if e["type"] == "consumes"]
        targets = {e["to"] for e in consumes}
        self.assertIn("channel:kafka:orders.placed", targets)

    def test_dynamic_topic_not_bound(self) -> None:
        """Dynamic first arg must be dropped per static-truth policy."""
        all_targets = {e["to"] for e in self.edges}
        # The dynamic call uses a variable, so no channel id should
        # appear for it. We check there is no channel with 'name' as
        # its literal -- the variable name is ``name``.
        for t in all_targets:
            self.assertNotIn("channel:kafka:name", t)

    def test_binding_edges_are_inferred(self) -> None:
        for e in self.edges:
            self.assertEqual(
                e["props"]["confidence"], "inferred", f"edge: {e}"
            )

    def test_bindings_fragment_validates(self) -> None:
        errs = validate_fragment(
            {"nodes": {}, "edges": self.edges},
            source_label="strategy:events_bindings",
            allow_dangling_edges=True,
        )
        self.assertEqual(errs, [], f"validation errors: {errs}")

if __name__ == "__main__":
    unittest.main()
