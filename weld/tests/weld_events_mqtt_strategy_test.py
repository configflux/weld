"""Tests for the MQTT channel producer/consumer strategy (tracked project).

The ``events_mqtt`` strategy statically recognizes paho.mqtt /
asyncio-mqtt (aiomqtt) ``publish()`` / ``subscribe()`` call sites and
emits ``channel:mqtt:<topic>`` nodes with directional
``produces`` / ``consumes`` edges. Detection is structural only per ADR
0018's static-truth policy:

- Producer: a call shaped ``<Root>.publish("literal", ...)`` where
  ``<Root>`` is an idiomatic MQTT client handle (``client``,
  ``mqtt_client``, ``mqttc``) in a file that imports an MQTT library.
  Emits a ``channel:mqtt:<topic>`` node plus a ``produces`` edge from
  the declaring file.

- Consumer: a call shaped ``<Root>.subscribe("literal")``. Emits a
  ``channel:mqtt:<topic>`` node plus a ``consumes`` edge.

Channel nodes reuse ``events_shared.channel_node`` so they carry
``protocol="event"``, ``surface_kind="pub_sub"``, ``transport="mqtt"``.
Edges carry ``source_strategy="events_mqtt"`` and
``confidence="inferred"`` -- the same node-definite / edge-inferred
split the rest of the events family uses. Dynamic first args (variables,
QoS tuples, topic lists) are dropped rather than guessed.
"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path


from weld.contract import validate_fragment  # noqa: E402
from weld.strategies.events_mqtt import extract  # noqa: E402


def _write(pkg: Path, name: str, body: str) -> None:
    (pkg / name).write_text(textwrap.dedent(body))


def _run(root: Path, py_glob: str = "**/*.py") -> tuple[dict, list, list]:
    result = extract(root, {"glob": py_glob}, {})
    return result.nodes, result.edges, list(result.discovered_from)


# ---------------------------------------------------------------------------
# Producer (publish) tests
# ---------------------------------------------------------------------------

class MqttProducerTest(unittest.TestCase):
    """paho / aiomqtt publish call sites emit nodes + ``produces`` edges."""

    def test_paho_publish_emits_channel_node_and_produces_edge(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "svc"
            pkg.mkdir()
            _write(pkg, "pub.py", """\
                import paho.mqtt.client as mqtt
                def emit():
                    client = mqtt.Client()
                    client.publish("sensors/temp", "21.5")
            """)
            nodes, edges, discovered = _run(root, "svc/*.py")
            # Channel node with mqtt interaction metadata.
            self.assertIn("channel:mqtt:sensors/temp", nodes)
            node = nodes["channel:mqtt:sensors/temp"]
            self.assertEqual(node["type"], "channel")
            self.assertEqual(node["props"]["transport"], "mqtt")
            self.assertEqual(node["props"]["protocol"], "event")
            self.assertEqual(node["props"]["surface_kind"], "pub_sub")
            self.assertEqual(node["props"]["name"], "sensors/temp")
            self.assertEqual(node["props"]["declared_in"], "svc/pub.py")
            # Directional produces edge.
            produces = [e for e in edges if e["type"] == "produces"]
            self.assertEqual(len(produces), 1)
            self.assertEqual(produces[0]["from"], "file:svc/pub")
            self.assertEqual(produces[0]["to"], "channel:mqtt:sensors/temp")
            self.assertEqual(
                produces[0]["props"]["source_strategy"], "events_mqtt"
            )
            self.assertEqual(produces[0]["props"]["confidence"], "inferred")
            self.assertIn("svc/pub.py", discovered)

    def test_asyncio_mqtt_publish_emits_produces_edge(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "svc"
            pkg.mkdir()
            _write(pkg, "apub.py", """\
                from asyncio_mqtt import Client
                async def emit():
                    async with Client("broker") as client:
                        await client.publish("events/orders", b"x")
            """)
            nodes, edges, _ = _run(root, "svc/*.py")
            self.assertIn("channel:mqtt:events/orders", nodes)
            produces = [e for e in edges if e["type"] == "produces"]
            self.assertEqual(len(produces), 1)
            self.assertEqual(produces[0]["to"], "channel:mqtt:events/orders")

    def test_mqttc_root_variant_matches(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "svc"
            pkg.mkdir()
            _write(pkg, "v.py", """\
                import paho.mqtt.client as mqtt
                def emit(mqttc):
                    mqttc.publish("a/b", "p")
            """)
            _, edges, _ = _run(root, "svc/*.py")
            produces = [e for e in edges if e["type"] == "produces"]
            self.assertEqual(len(produces), 1)
            self.assertEqual(produces[0]["to"], "channel:mqtt:a/b")


# ---------------------------------------------------------------------------
# Consumer (subscribe) tests
# ---------------------------------------------------------------------------

class MqttConsumerTest(unittest.TestCase):
    """paho / aiomqtt subscribe call sites emit nodes + ``consumes`` edges."""

    def test_aiomqtt_subscribe_emits_channel_node_and_consumes_edge(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "svc"
            pkg.mkdir()
            _write(pkg, "sub.py", """\
                from aiomqtt import Client
                async def listen():
                    async with Client("broker") as client:
                        await client.subscribe("sensors/temp")
            """)
            nodes, edges, discovered = _run(root, "svc/*.py")
            self.assertIn("channel:mqtt:sensors/temp", nodes)
            consumes = [e for e in edges if e["type"] == "consumes"]
            self.assertEqual(len(consumes), 1)
            self.assertEqual(consumes[0]["from"], "file:svc/sub")
            self.assertEqual(consumes[0]["to"], "channel:mqtt:sensors/temp")
            self.assertEqual(
                consumes[0]["props"]["source_strategy"], "events_mqtt"
            )
            self.assertEqual(consumes[0]["props"]["confidence"], "inferred")
            self.assertIn("svc/sub.py", discovered)

    def test_paho_subscribe_emits_consumes_edge(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "svc"
            pkg.mkdir()
            _write(pkg, "psub.py", """\
                import paho.mqtt.client as mqtt
                def listen():
                    client = mqtt.Client()
                    client.subscribe("commands/reboot")
            """)
            _, edges, _ = _run(root, "svc/*.py")
            consumes = [e for e in edges if e["type"] == "consumes"]
            self.assertEqual(len(consumes), 1)
            self.assertEqual(consumes[0]["to"], "channel:mqtt:commands/reboot")

    def test_publish_and_subscribe_same_topic_collapse_one_node(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "svc"
            pkg.mkdir()
            _write(pkg, "both.py", """\
                import paho.mqtt.client as mqtt
                def roundtrip(client):
                    client.publish("bus/x", "p")
                    client.subscribe("bus/x")
            """)
            nodes, edges, _ = _run(root, "svc/*.py")
            self.assertEqual(list(nodes), ["channel:mqtt:bus/x"])
            kinds = sorted(e["type"] for e in edges)
            self.assertEqual(kinds, ["consumes", "produces"])


# ---------------------------------------------------------------------------
# Negative cases: unrelated calls must not emit channels
# ---------------------------------------------------------------------------

class MqttNegativeTest(unittest.TestCase):
    """Only mqtt publish/subscribe on a known root in an mqtt-importing file."""

    def test_non_mqtt_import_is_ignored(self) -> None:
        """A ``client.publish`` in a file that never imports mqtt is dropped."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "svc"
            pkg.mkdir()
            _write(pkg, "other.py", """\
                import redis
                def emit(client):
                    client.publish("notify:users", "hi")
            """)
            nodes, edges, discovered = _run(root, "svc/*.py")
            self.assertEqual((nodes, edges, discovered), ({}, [], []))

    def test_unrelated_verb_is_ignored(self) -> None:
        """``client.connect(...)`` is not a publish/subscribe verb."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "svc"
            pkg.mkdir()
            _write(pkg, "conn.py", """\
                import paho.mqtt.client as mqtt
                def go(client):
                    client.connect("broker", 1883)
                    client.loop_forever()
            """)
            _, edges, _ = _run(root, "svc/*.py")
            self.assertEqual(edges, [])

    def test_unrelated_receiver_is_ignored(self) -> None:
        """A non-client receiver calling publish is not an mqtt channel."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "svc"
            pkg.mkdir()
            _write(pkg, "recv.py", """\
                import paho.mqtt.client as mqtt
                def go(bus):
                    bus.publish("topic/x", "p")
            """)
            nodes, edges, _ = _run(root, "svc/*.py")
            self.assertEqual((nodes, edges), ({}, []))

    def test_dynamic_topic_is_dropped(self) -> None:
        """A non-literal topic argument is dropped (static-truth)."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "svc"
            pkg.mkdir()
            _write(pkg, "dyn.py", """\
                import paho.mqtt.client as mqtt
                def emit(client, topic):
                    client.publish(topic, "p")
                    client.subscribe(topic)
            """)
            nodes, edges, discovered = _run(root, "svc/*.py")
            self.assertEqual((nodes, edges, discovered), ({}, [], []))

    def test_subscribe_qos_tuple_is_dropped(self) -> None:
        """paho's ``subscribe(("topic", qos))`` tuple form is not a literal."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "svc"
            pkg.mkdir()
            _write(pkg, "qos.py", """\
                import paho.mqtt.client as mqtt
                def listen(client):
                    client.subscribe(("sensors/temp", 1))
            """)
            nodes, edges, _ = _run(root, "svc/*.py")
            self.assertEqual((nodes, edges), ({}, []))


# ---------------------------------------------------------------------------
# Fragment validation
# ---------------------------------------------------------------------------

class MqttFragmentValidatesTest(unittest.TestCase):
    """Strategy output must pass contract.validate_fragment."""

    def test_fragment_is_contract_valid(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "svc"
            pkg.mkdir()
            _write(pkg, "pub.py", """\
                import paho.mqtt.client as mqtt
                def emit(client):
                    client.publish("sensors/temp", "21")
            """)
            _write(pkg, "sub.py", """\
                from aiomqtt import Client
                async def listen(client):
                    await client.subscribe("sensors/temp")
            """)
            nodes, edges, _ = _run(root, "svc/*.py")
            fragment = {"nodes": nodes, "edges": edges, "discovered_from": []}
            errors = validate_fragment(
                fragment,
                source_label="strategy:events_mqtt",
                allow_dangling_edges=True,
            )
            self.assertEqual(errors, [], f"unexpected errors: {errors}")


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------

class MqttRobustnessTest(unittest.TestCase):
    """Graceful degradation on missing/malformed input."""

    def test_no_mqtt_imports_yields_empty(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "svc"
            pkg.mkdir()
            _write(pkg, "plain.py", "x = 1\n")
            self.assertEqual(_run(root, "svc/*.py"), ({}, [], []))

    def test_missing_glob_yields_empty(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            result = extract(root, {}, {})
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])
            self.assertEqual(list(result.discovered_from), [])

    def test_importing_file_with_no_calls_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "svc"
            pkg.mkdir()
            _write(pkg, "noop.py", """\
                import paho.mqtt.client as mqtt
                x = mqtt
            """)
            nodes, edges, discovered = _run(root, "svc/*.py")
            self.assertEqual((nodes, edges, discovered), ({}, [], []))


if __name__ == "__main__":
    unittest.main()
