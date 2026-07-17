"""Full-discovery regression: events-family binding/contains edges must survive.

Reproduces the ``events``-family edge-drop bug: the four family emit sites
minted the edge ``from`` endpoint as ``file:<rel-WITH-.py>`` while
``python_module`` mints the canonical file node as
``file:<rel-WITHOUT-extension>``. Because
``weld._discover_postprocess._clean_and_dedup_edges`` prunes any edge whose
endpoint is not a node id (exact match, no extension normalization), every
``produces`` / ``consumes`` / ``contains``->channel edge was swept during
post-process while the channel *nodes* (canonical) survived. The symptom is
zero surviving file->channel binding edges after a full discover.

This test drives the *real* discover pipeline (``python_module`` + ``events``
py_callsite + ``events_bindings`` + ``events_mqtt``) over a fixture with a
Kafka producer/consumer and an MQTT publish/subscribe, then asserts the
binding/contains edges survive the full post-process with canonical
extensionless endpoints that match the ``python_module`` file nodes. It fails
(zero surviving binding edges) before the shared-helper edge-key fix.
"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from weld.discover import discover  # noqa: E402

# A fully-wired discover config: the file-node minter (``python_module``)
# plus all three Python events-family strategies that bind files to channels.
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
    "  - strategy: events_mqtt\n"
    "    glob: svc/**/*.py\n"
    "    type: channel\n"
)

# Kafka producer (events_callsite -> contains, events_bindings -> produces),
# Kafka consumer (events_bindings -> consumes), and an MQTT publish+subscribe
# service (events_mqtt -> produces + consumes). Every file has a public
# function so ``python_module`` mints its canonical ``file:`` node.
_FILES = {
    "svc/producer.py": """\
        from kafka import KafkaProducer

        def send_order():
            KafkaProducer.send("orders.events", b"payload")
    """,
    "svc/consumer.py": """\
        from kafka import KafkaConsumer

        def consume_orders():
            KafkaConsumer.subscribe(["orders.events"])
    """,
    "svc/mqtt_svc.py": """\
        import paho.mqtt.client as mqtt

        def emit():
            client = mqtt.Client()
            client.publish("sensors/temp", "21.5")

        def listen():
            client = mqtt.Client()
            client.subscribe("commands/reboot")
    """,
}


def _build_fixture(root: Path) -> None:
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    (root / ".weld" / "discover.yaml").write_text(
        _DISCOVER_YAML, encoding="utf-8"
    )
    for rel, body in _FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body), encoding="utf-8")


class EventsBindingEdgesSurviveDiscoverTest(unittest.TestCase):
    """file->channel binding/contains edges survive the full post-process."""

    graph: dict
    nodes: dict
    edges: list

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        _build_fixture(root)
        cls.graph = discover(root, incremental=False)
        cls.nodes = cls.graph["nodes"]
        cls.edges = cls.graph["edges"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()
        super().tearDownClass()

    def _has_edge(self, etype: str, frm: str, to: str) -> bool:
        return any(
            e["type"] == etype and e["from"] == frm and e["to"] == to
            for e in self.edges
        )

    # -- Baseline: the channel *nodes* survive today (canonical ids). -------
    def test_channel_nodes_present(self) -> None:
        for cid in (
            "channel:kafka:orders.events",
            "channel:mqtt:sensors/temp",
            "channel:mqtt:commands/reboot",
        ):
            self.assertIn(cid, self.nodes, f"missing channel node {cid}")

    # -- python_module mints canonical extensionless file nodes. ------------
    def test_canonical_file_nodes_present(self) -> None:
        for fid in (
            "file:svc/producer",
            "file:svc/consumer",
            "file:svc/mqtt_svc",
        ):
            self.assertIn(fid, self.nodes, f"missing file node {fid}")

    # -- The regression: binding/contains edges must survive. ---------------
    def test_kafka_contains_edge_survives(self) -> None:
        self.assertTrue(
            self._has_edge(
                "contains", "file:svc/producer", "channel:kafka:orders.events"
            ),
            "events (callsite) contains edge was swept by post-process",
        )

    def test_kafka_produces_edge_survives(self) -> None:
        self.assertTrue(
            self._has_edge(
                "produces", "file:svc/producer", "channel:kafka:orders.events"
            ),
            "events_bindings produces edge was swept by post-process",
        )

    def test_kafka_consumes_edge_survives(self) -> None:
        self.assertTrue(
            self._has_edge(
                "consumes", "file:svc/consumer", "channel:kafka:orders.events"
            ),
            "events_bindings consumes edge was swept by post-process",
        )

    def test_mqtt_produces_edge_survives(self) -> None:
        self.assertTrue(
            self._has_edge(
                "produces", "file:svc/mqtt_svc", "channel:mqtt:sensors/temp"
            ),
            "events_mqtt produces edge was swept by post-process",
        )

    def test_mqtt_consumes_edge_survives(self) -> None:
        self.assertTrue(
            self._has_edge(
                "consumes", "file:svc/mqtt_svc", "channel:mqtt:commands/reboot"
            ),
            "events_mqtt consumes edge was swept by post-process",
        )

    # -- Guard: no surviving events edge may carry a ``.py`` endpoint. ------
    #    (Also fails if the sweep is later loosened to normalize extensions
    #    rather than fixing the mint side.)
    def test_no_binding_edge_has_extension_endpoint(self) -> None:
        offenders = [
            e
            for e in self.edges
            if e["type"] in {"produces", "consumes", "contains"}
            and str(e["to"]).startswith("channel:")
            and str(e["from"]).endswith(".py")
        ]
        self.assertEqual(
            offenders, [], f"extension-bearing endpoints survived: {offenders}"
        )


# A repo with a kafka *consumer only* -- no local producer, no compose
# declaration. Before symmetric consume-site node minting, events_callsite
# minted the channel node only at producer sites, so this repo retained
# neither the node nor its (dangling) consumes edge after its own discover.
_CONSUMER_ONLY_FILES = {
    "svc/consumer.py": """\
        from kafka import KafkaConsumer

        def consume_orders():
            KafkaConsumer.subscribe(["orders.events"])
    """,
}


class ConsumerOnlyKafkaChannelSurvivesDiscoverTest(unittest.TestCase):
    """A consumer-only kafka repo retains the channel node + consumes edge.

    Direct regression for the ADR 0090 follow-up: the channel node must be
    minted at the subscribe site so it survives a full discover with no
    local producer, which is what lets the cross-repo ``channel_binding``
    resolver match this consumer.
    """

    nodes: dict
    edges: list

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        (root / ".weld").mkdir(parents=True, exist_ok=True)
        (root / ".weld" / "discover.yaml").write_text(
            _DISCOVER_YAML, encoding="utf-8"
        )
        for rel, body in _CONSUMER_ONLY_FILES.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(body), encoding="utf-8")
        graph = discover(root, incremental=False)
        cls.nodes = graph["nodes"]
        cls.edges = graph["edges"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()
        super().tearDownClass()

    def test_consumer_only_channel_node_survives(self) -> None:
        self.assertIn(
            "channel:kafka:orders.events",
            self.nodes,
            "consumer-only channel node was not minted / did not survive",
        )

    def test_consumer_only_consumes_edge_survives(self) -> None:
        self.assertTrue(
            any(
                e["type"] == "consumes"
                and e["from"] == "file:svc/consumer"
                and e["to"] == "channel:kafka:orders.events"
                for e in self.edges
            ),
            "consumes edge was swept (dangling) with no producer present",
        )


if __name__ == "__main__":
    unittest.main()
