"""Tests for the channel_binding cross-repo resolver (ADR 0090).

Covers the acceptance criteria:

* A producer in child A and a consumer in child B on the same channel id
  emit a ``cross_repo:channel_flow`` edge (producer -> consumer).
* Different topics produce no edge.
* A producer/consumer pair within the *same* child produces no cross edge
  (the in-repo ``feeds_into`` join owns that).
* Output is deterministic across runs and child insertion order.
* Registration under ``channel_binding``.
* An end-to-end MQTT federation: two independently *discovered* child
  graphs (a publisher repo and a subscriber repo) join through the real
  channel node that ``events_mqtt`` mints at both ends.
* An end-to-end kafka federation where the subscriber is *consumer-only*
  (no local producer): the child joins through the channel node that
  ``events_callsite`` now mints at the subscribe site.
"""

from __future__ import annotations

import json
import tempfile
import textwrap
import unittest
from pathlib import Path

from weld.cross_repo.base import CrossRepoEdge, ResolverContext, run_resolvers

# Import for registration side effect.
import weld.cross_repo.channel_binding as _channel_mod  # noqa: F401
from weld.cross_repo import resolver_names
from weld.discover import discover
from weld.workspace import UNIT_SEPARATOR as SEP


class _FakeGraph:
    """Minimal stand-in for :class:`weld.graph.Graph` with ``_data``."""

    def __init__(self, nodes: dict | None = None, edges: list | None = None) -> None:
        self._data = {"nodes": dict(nodes or {}), "edges": list(edges or [])}


def _make_context(
    children: dict[str, _FakeGraph],
    *,
    strategies: list[str] | None = None,
) -> ResolverContext:
    raw = {n: json.dumps(g._data).encode() for n, g in children.items()}
    hashes = {n: ResolverContext.hash_bytes(b) for n, b in raw.items()}
    return ResolverContext(
        workspace_root="/tmp/ws",
        cross_repo_strategies=(
            strategies if strategies is not None else ["channel_binding"]
        ),
        children=children,
        child_hashes=hashes,
    )


def _channel(topic: str, transport: str = "kafka") -> dict:
    return {
        "type": "channel",
        "label": topic,
        "props": {"transport": transport, "name": topic},
    }


def _producer_child(topic: str, file_id: str = "file:pub") -> _FakeGraph:
    cid = f"channel:kafka:{topic}"
    return _FakeGraph(
        nodes={file_id: {"type": "file", "label": "pub", "props": {}}, cid: _channel(topic)},
        edges=[{"from": file_id, "to": cid, "type": "produces",
                "props": {"source_strategy": "events_bindings", "confidence": "inferred"}}],
    )


def _consumer_child(topic: str, file_id: str = "file:sub") -> _FakeGraph:
    cid = f"channel:kafka:{topic}"
    return _FakeGraph(
        nodes={file_id: {"type": "file", "label": "sub", "props": {}}, cid: _channel(topic)},
        edges=[{"from": file_id, "to": cid, "type": "consumes",
                "props": {"source_strategy": "events_bindings", "confidence": "inferred"}}],
    )


class RegistrationTests(unittest.TestCase):
    def test_registered(self) -> None:
        self.assertIn("channel_binding", resolver_names())


class MatchTests(unittest.TestCase):
    def test_producer_a_consumer_b_join(self) -> None:
        ctx = _make_context(
            {"svc-a": _producer_child("orders"), "svc-b": _consumer_child("orders")}
        )
        edges = run_resolvers(ctx)
        self.assertEqual(len(edges), 1)
        edge = edges[0]
        self.assertEqual(edge.type, "cross_repo:channel_flow")
        self.assertEqual(edge.from_id, f"svc-a{SEP}file:pub")
        self.assertEqual(edge.to_id, f"svc-b{SEP}file:sub")
        self.assertEqual(edge.props["channel"], "channel:kafka:orders")
        self.assertEqual(edge.props["transport"], "kafka")
        self.assertEqual(edge.props["topic"], "orders")
        self.assertEqual(edge.props["confidence"], "inferred")
        self.assertEqual(edge.props["source_strategy"], "channel_binding")

    def test_different_topics_no_edge(self) -> None:
        ctx = _make_context(
            {"svc-a": _producer_child("orders"), "svc-b": _consumer_child("invoices")}
        )
        self.assertEqual(run_resolvers(ctx), [])

    def test_same_child_producer_consumer_no_cross_edge(self) -> None:
        """In-repo pairs are the feeds_into join's job, not this resolver's."""
        cid = "channel:kafka:orders"
        child = _FakeGraph(
            nodes={
                "file:pub": {"type": "file", "label": "pub", "props": {}},
                "file:sub": {"type": "file", "label": "sub", "props": {}},
                cid: _channel("orders"),
            },
            edges=[
                {"from": "file:pub", "to": cid, "type": "produces",
                 "props": {"source_strategy": "events_bindings", "confidence": "inferred"}},
                {"from": "file:sub", "to": cid, "type": "consumes",
                 "props": {"source_strategy": "events_bindings", "confidence": "inferred"}},
            ],
        )
        ctx = _make_context({"svc-a": child})
        self.assertEqual(run_resolvers(ctx), [])

    def test_single_and_empty_children(self) -> None:
        self.assertEqual(run_resolvers(_make_context({})), [])
        self.assertEqual(
            run_resolvers(_make_context({"svc-a": _producer_child("orders")})), []
        )

    def test_fan_out_multiple_consumers(self) -> None:
        ctx = _make_context({
            "svc-a": _producer_child("orders"),
            "svc-b": _consumer_child("orders", file_id="file:sub_b"),
            "svc-c": _consumer_child("orders", file_id="file:sub_c"),
        })
        edges = run_resolvers(ctx)
        self.assertEqual(len(edges), 2)
        self.assertEqual({e.to_id for e in edges},
                         {f"svc-b{SEP}file:sub_b", f"svc-c{SEP}file:sub_c"})

    def test_absent_strategy_no_edges(self) -> None:
        ctx = _make_context(
            {"svc-a": _producer_child("orders"), "svc-b": _consumer_child("orders")},
            strategies=[],
        )
        self.assertEqual(run_resolvers(ctx), [])


class DeterminismTests(unittest.TestCase):
    def test_two_runs_identical(self) -> None:
        def run() -> list[dict]:
            ctx = _make_context(
                {"svc-a": _producer_child("orders"), "svc-b": _consumer_child("orders")}
            )
            return [e.to_dict() for e in run_resolvers(ctx)]
        self.assertEqual(json.dumps(run(), sort_keys=True), json.dumps(run(), sort_keys=True))

    def test_child_order_irrelevant(self) -> None:
        a, b = _producer_child("orders"), _consumer_child("orders")
        e1 = [e.to_dict() for e in run_resolvers(_make_context({"svc-a": a, "svc-b": b}))]
        e2 = [e.to_dict() for e in run_resolvers(_make_context({"svc-b": b, "svc-a": a}))]
        self.assertEqual(e1, e2)


class EdgeContractTests(unittest.TestCase):
    def test_edge_is_frozen(self) -> None:
        ctx = _make_context(
            {"svc-a": _producer_child("orders"), "svc-b": _consumer_child("orders")}
        )
        edge = run_resolvers(ctx)[0]
        with self.assertRaises(AttributeError):
            edge.from_id = "tampered"  # type: ignore[misc]

    def test_to_dict_round_trips(self) -> None:
        ctx = _make_context(
            {"svc-a": _producer_child("orders"), "svc-b": _consumer_child("orders")}
        )
        edge = run_resolvers(ctx)[0]
        again = CrossRepoEdge.from_mapping(edge.to_dict())
        self.assertEqual(again.from_id, edge.from_id)
        self.assertEqual(again.type, edge.type)
        self.assertEqual(dict(again.props), dict(edge.props))


# --- End-to-end MQTT federation over real discovered child graphs ----------

_MQTT_YAML = (
    "sources:\n"
    "  - strategy: python_module\n"
    "    glob: svc/**/*.py\n"
    "    type: file\n"
    "  - strategy: events_mqtt\n"
    "    glob: svc/**/*.py\n"
    "    type: channel\n"
)

_PUB_FILE = """\
    import paho.mqtt.client as mqtt

    def emit():
        client = mqtt.Client()
        client.publish("commands/reboot", "now")
"""

_SUB_FILE = """\
    import paho.mqtt.client as mqtt

    def listen():
        client = mqtt.Client()
        client.subscribe("commands/reboot")
"""


def _discover_child(tmp: Path, filename: str, body: str) -> _FakeGraph:
    (tmp / ".weld").mkdir(parents=True, exist_ok=True)
    (tmp / ".weld" / "discover.yaml").write_text(_MQTT_YAML, encoding="utf-8")
    path = tmp / "svc" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    graph = discover(tmp, incremental=False)
    return _FakeGraph(nodes=graph["nodes"], edges=graph["edges"])


class MqttFederationIntegrationTest(unittest.TestCase):
    """A publisher repo and a subscriber repo join through the real node."""

    def test_cross_repo_channel_flow_over_real_discover(self) -> None:
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            pub = _discover_child(Path(a), "pub.py", _PUB_FILE)
            sub = _discover_child(Path(b), "sub.py", _SUB_FILE)
            # Sanity: the subscriber repo really retains the mqtt channel
            # node (events_mqtt mints it at the subscribe site too).
            self.assertIn("channel:mqtt:commands/reboot", sub._data["nodes"])

            ctx = _make_context({"svc-pub": pub, "svc-sub": sub})
            edges = run_resolvers(ctx)
            self.assertEqual(len(edges), 1)
            edge = edges[0]
            self.assertEqual(edge.type, "cross_repo:channel_flow")
            self.assertEqual(edge.from_id, f"svc-pub{SEP}file:svc/pub")
            self.assertEqual(edge.to_id, f"svc-sub{SEP}file:svc/sub")
            self.assertEqual(edge.props["channel"], "channel:mqtt:commands/reboot")
            self.assertEqual(edge.props["transport"], "mqtt")


# --- End-to-end Kafka consumer-only federation over real discovered graphs --
#
# The consumer child has no local producer and no compose declaration, so it
# retains the channel node only because events_callsite now mints it at the
# subscribe site. Before that fix the node (and its then-dangling consumes
# edge) were swept during the consumer child's own discover, leaving nothing
# for channel_binding to match on.

_KAFKA_YAML = (
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

_KAFKA_PUB_FILE = """\
    from kafka import KafkaProducer

    def emit():
        KafkaProducer.send("orders.events", b"payload")
"""

_KAFKA_SUB_FILE = """\
    from kafka import KafkaConsumer

    def listen():
        KafkaConsumer.subscribe(["orders.events"])
"""


def _discover_kafka_child(tmp: Path, filename: str, body: str) -> _FakeGraph:
    (tmp / ".weld").mkdir(parents=True, exist_ok=True)
    (tmp / ".weld" / "discover.yaml").write_text(_KAFKA_YAML, encoding="utf-8")
    path = tmp / "svc" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    graph = discover(tmp, incremental=False)
    return _FakeGraph(nodes=graph["nodes"], edges=graph["edges"])


class KafkaConsumerOnlyFederationIntegrationTest(unittest.TestCase):
    """A kafka producer repo and a *consumer-only* repo join cross-repo.

    Mirrors :class:`MqttFederationIntegrationTest`, but the channel node is
    minted by events_callsite (not events_mqtt). The consumer repo has no
    producer, so it retains the node solely via symmetric consume-site
    minting -- the change this test guards.
    """

    def test_cross_repo_channel_flow_over_real_discover(self) -> None:
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            pub = _discover_kafka_child(Path(a), "pub.py", _KAFKA_PUB_FILE)
            sub = _discover_kafka_child(Path(b), "sub.py", _KAFKA_SUB_FILE)
            # The consumer-only repo retains the kafka channel node solely
            # because events_callsite mints it at the subscribe site.
            self.assertIn("channel:kafka:orders.events", sub._data["nodes"])
            # And its consumes edge survived post-process (did not dangle).
            self.assertTrue(
                any(
                    e["type"] == "consumes"
                    and e["to"] == "channel:kafka:orders.events"
                    for e in sub._data["edges"]
                ),
                "consumer child dropped its consumes edge",
            )

            ctx = _make_context({"svc-pub": pub, "svc-sub": sub})
            edges = run_resolvers(ctx)
            self.assertEqual(len(edges), 1)
            edge = edges[0]
            self.assertEqual(edge.type, "cross_repo:channel_flow")
            self.assertEqual(edge.from_id, f"svc-pub{SEP}file:svc/pub")
            self.assertEqual(edge.to_id, f"svc-sub{SEP}file:svc/sub")
            self.assertEqual(
                edge.props["channel"], "channel:kafka:orders.events"
            )
            self.assertEqual(edge.props["transport"], "kafka")
            self.assertEqual(edge.props["topic"], "orders.events")


if __name__ == "__main__":
    unittest.main()
