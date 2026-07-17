"""Tests for the in-repo producer->consumer channel join (ADR 0090).

Two layers:

* Unit -- drive ``link_producers_consumers`` directly over hand-built
  ``nodes``/``edges`` for the join logic, the self-loop skip, the
  different-topic negative, the dangling-channel guard, idempotency, and
  determinism.
* Integration -- run the *real* discover pipeline over a fixture with a
  Kafka publisher module and a Kafka subscriber module on the same topic
  and assert the derived ``feeds_into`` edge survives post-process; a
  second topic pair on *different* topics asserts no cross join.
"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from weld._events_join import (
    JOIN_EDGE_TYPE,
    JOIN_SOURCE_STRATEGY,
    link_producers_consumers,
)
from weld.discover import discover


def _channel_node(topic: str, transport: str = "kafka") -> dict:
    return {
        "type": "channel",
        "label": topic,
        "props": {"transport": transport, "name": topic},
    }


def _file_node(label: str) -> dict:
    return {"type": "file", "label": label, "props": {}}


def _bind(src: str, dst: str, etype: str) -> dict:
    return {
        "from": src,
        "to": dst,
        "type": etype,
        "props": {"source_strategy": "events_bindings", "confidence": "inferred"},
    }


def _feeds_into(edges: list[dict]) -> list[dict]:
    return [e for e in edges if e["type"] == JOIN_EDGE_TYPE]


class LinkUnitTests(unittest.TestCase):
    """Direct exercise of ``link_producers_consumers``."""

    def _base(self) -> tuple[dict, list[dict]]:
        nodes = {
            "file:a": _file_node("a"),
            "file:b": _file_node("b"),
            "channel:kafka:orders": _channel_node("orders"),
        }
        edges = [
            _bind("file:a", "channel:kafka:orders", "produces"),
            _bind("file:b", "channel:kafka:orders", "consumes"),
        ]
        return nodes, edges

    def test_producer_consumer_join_emitted(self) -> None:
        nodes, edges = self._base()
        link_producers_consumers(nodes, edges)
        joins = _feeds_into(edges)
        self.assertEqual(len(joins), 1)
        edge = joins[0]
        self.assertEqual(edge["from"], "file:a")
        self.assertEqual(edge["to"], "file:b")
        self.assertEqual(edge["props"]["source_strategy"], JOIN_SOURCE_STRATEGY)
        self.assertEqual(edge["props"]["confidence"], "inferred")
        self.assertEqual(edge["props"]["channel"], "channel:kafka:orders")
        self.assertEqual(edge["props"]["transport"], "kafka")
        self.assertEqual(edge["props"]["topic"], "orders")

    def test_self_producer_consumer_no_self_loop(self) -> None:
        """A module that both produces and consumes the topic is not joined."""
        nodes = {
            "file:a": _file_node("a"),
            "channel:kafka:orders": _channel_node("orders"),
        }
        edges = [
            _bind("file:a", "channel:kafka:orders", "produces"),
            _bind("file:a", "channel:kafka:orders", "consumes"),
        ]
        link_producers_consumers(nodes, edges)
        self.assertEqual(_feeds_into(edges), [])

    def test_different_topics_no_join(self) -> None:
        nodes = {
            "file:a": _file_node("a"),
            "file:b": _file_node("b"),
            "channel:kafka:orders": _channel_node("orders"),
            "channel:kafka:invoices": _channel_node("invoices"),
        }
        edges = [
            _bind("file:a", "channel:kafka:orders", "produces"),
            _bind("file:b", "channel:kafka:invoices", "consumes"),
        ]
        link_producers_consumers(nodes, edges)
        self.assertEqual(_feeds_into(edges), [])

    def test_dangling_channel_not_joined(self) -> None:
        """A produces/consumes edge to an absent channel node is ignored."""
        nodes = {"file:a": _file_node("a"), "file:b": _file_node("b")}
        edges = [
            _bind("file:a", "channel:kafka:ghost", "produces"),
            _bind("file:b", "channel:kafka:ghost", "consumes"),
        ]
        link_producers_consumers(nodes, edges)
        self.assertEqual(_feeds_into(edges), [])

    def test_non_channel_target_not_joined(self) -> None:
        """produces/consumes into a non-``channel`` node (e.g. ros_topic)."""
        nodes = {
            "file:a": _file_node("a"),
            "file:b": _file_node("b"),
            "ros_topic:/scan": {"type": "ros_topic", "label": "/scan", "props": {}},
        }
        edges = [
            _bind("file:a", "ros_topic:/scan", "produces"),
            _bind("file:b", "ros_topic:/scan", "consumes"),
        ]
        link_producers_consumers(nodes, edges)
        self.assertEqual(_feeds_into(edges), [])

    def test_fan_out_cartesian(self) -> None:
        nodes = {
            "file:p1": _file_node("p1"),
            "file:p2": _file_node("p2"),
            "file:c1": _file_node("c1"),
            "file:c2": _file_node("c2"),
            "channel:kafka:orders": _channel_node("orders"),
        }
        edges = [
            _bind("file:p1", "channel:kafka:orders", "produces"),
            _bind("file:p2", "channel:kafka:orders", "produces"),
            _bind("file:c1", "channel:kafka:orders", "consumes"),
            _bind("file:c2", "channel:kafka:orders", "consumes"),
        ]
        link_producers_consumers(nodes, edges)
        pairs = {(e["from"], e["to"]) for e in _feeds_into(edges)}
        self.assertEqual(
            pairs,
            {
                ("file:p1", "file:c1"), ("file:p1", "file:c2"),
                ("file:p2", "file:c1"), ("file:p2", "file:c2"),
            },
        )

    def test_idempotent_and_deterministic(self) -> None:
        """Re-running strips prior output and re-derives byte-identically."""
        nodes, edges = self._base()
        link_producers_consumers(nodes, edges)
        first = _feeds_into(edges)
        # Second run over the already-joined edge list (mirrors the
        # incremental path carrying prior edges forward).
        link_producers_consumers(nodes, edges)
        second = _feeds_into(edges)
        self.assertEqual(len(second), 1)
        self.assertEqual(first, second)


# --- Integration: real discover pipeline -----------------------------------

_DISCOVER_YAML = (
    "sources:\n"
    "  - strategy: python_module\n"
    "    glob: svc/**/*.py\n"
    "    type: file\n"
    "  - strategy: events\n"
    "    glob: svc/**/*.py\n"
    "    type: channel\n"
    "    kind: py_callsite\n"
    "  - strategy: events_bindings\n"
    "    glob: svc/**/*.py\n"
    "    type: file\n"
)

_FILES = {
    # Same topic -> should be joined.
    "svc/order_pub.py": """\
        from kafka import KafkaProducer

        def send_order():
            KafkaProducer.send("orders.events", b"payload")
    """,
    "svc/order_sub.py": """\
        from kafka import KafkaConsumer

        def consume_orders():
            KafkaConsumer.subscribe(["orders.events"])
    """,
    # Different topic -> must NOT be joined to the orders pair.
    "svc/invoice_sub.py": """\
        from kafka import KafkaConsumer

        def consume_invoices():
            KafkaConsumer.subscribe(["invoices.events"])
    """,
}


def _build_fixture(root: Path) -> None:
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    (root / ".weld" / "discover.yaml").write_text(_DISCOVER_YAML, encoding="utf-8")
    for rel, body in _FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body), encoding="utf-8")


class JoinDiscoverIntegrationTest(unittest.TestCase):
    """The derived ``feeds_into`` edge survives a full discover."""

    edges: list

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        _build_fixture(root)
        graph = discover(root, incremental=False)
        cls.edges = graph["edges"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()
        super().tearDownClass()

    def _has_feeds_into(self, frm: str, to: str) -> bool:
        return any(
            e["type"] == JOIN_EDGE_TYPE
            and e["from"] == frm
            and e["to"] == to
            and e["props"].get("source_strategy") == JOIN_SOURCE_STRATEGY
            for e in self.edges
        )

    def test_same_topic_pair_joined(self) -> None:
        self.assertTrue(
            self._has_feeds_into("file:svc/order_pub", "file:svc/order_sub"),
            "producer->consumer feeds_into edge missing after discover",
        )

    def test_different_topic_pair_not_joined(self) -> None:
        self.assertFalse(
            self._has_feeds_into("file:svc/order_pub", "file:svc/invoice_sub"),
            "producer must not feed a consumer on a different topic",
        )

    def test_join_is_deterministic_across_runs(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        try:
            root = Path(tmp.name)
            _build_fixture(root)
            first = discover(root, incremental=False)["edges"]
            second = discover(root, incremental=False)["edges"]
            self.assertEqual(first, second)
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
