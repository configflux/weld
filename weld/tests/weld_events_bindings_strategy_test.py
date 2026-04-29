"""Tests for the channel producer/consumer/payload linking strategy (tracked project).

The ``events_bindings`` strategy statically links Python producer and
consumer call sites back to ``channel:<transport>:<name>`` node ids
emitted by the ``events`` strategy (tracked project). Detection is
structural only per ADR 0018's static-truth policy:

- Producer binding: a call shaped ``<Root>.<verb>("literal", ...)``
  where ``<Root>`` is a known async client and ``<verb>`` is a known
  publish verb (``send``, ``produce``, ``publish``, etc.). Emits a
  ``produces`` edge from the declaring file to the channel node.

- Consumer binding: a call shaped ``<Root>.subscribe(["literal"])``
  or ``<Root>.subscribe("literal")`` where ``<Root>`` is a known async
  consumer identifier. Emits a ``consumes`` edge from the declaring
  file to the channel node.

- Payload contract linking: when the enclosing function has a typed
  parameter whose annotation looks like a contract class (uppercase,
  not a primitive), an ``implements`` edge is emitted from the channel
  node to the inferred contract name. This is ``confidence="inferred"``
  because we cannot verify BaseModel inheritance from one file.

All edges carry ``source_strategy="events_bindings"`` and
``confidence="inferred"``. Edges intentionally dangle against channel
node ids -- discovery's edge sweep resolves them when the ``events``
fragment is in the graph.
"""

from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.contract import validate_fragment  # noqa: E402
from weld.strategies.events_bindings import extract  # noqa: E402

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "events_sample"

def _write(pkg: Path, name: str, body: str) -> None:
    (pkg / name).write_text(textwrap.dedent(body))

def _run(root: Path, py_glob: str = "**/*.py") -> tuple[dict, list, list]:
    result = extract(root, {"glob": py_glob}, {})
    return result.nodes, result.edges, list(result.discovered_from)

# ---------------------------------------------------------------------------
# Producer binding tests
# ---------------------------------------------------------------------------

class EventsProducerBindingTest(unittest.TestCase):
    """Python publish/send call sites emit ``produces`` edges."""

    def test_kafka_send_emits_produces_edge(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "svc"
            pkg.mkdir()
            _write(pkg, "pub.py", """\
                from kafka import KafkaProducer
                def send_order():
                    KafkaProducer.send("orders.events", b"payload")
            """)
            _, edges, discovered = _run(root, "svc/*.py")
            produces = [e for e in edges if e["type"] == "produces"]
            self.assertEqual(len(produces), 1)
            self.assertEqual(produces[0]["from"], "file:svc/pub.py")
            self.assertEqual(produces[0]["to"], "channel:kafka:orders.events")
            self.assertEqual(produces[0]["props"]["confidence"], "inferred")
            self.assertEqual(
                produces[0]["props"]["source_strategy"], "events_bindings"
            )
            self.assertIn("svc/pub.py", discovered)

    def test_redis_publish_emits_produces_edge(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "svc"
            pkg.mkdir()
            _write(pkg, "notifier.py", """\
                import redis
                def broadcast():
                    redis.publish("notify:users", "hello")
            """)
            _, edges, _ = _run(root, "svc/*.py")
            produces = [e for e in edges if e["type"] == "produces"]
            self.assertEqual(len(produces), 1)
            self.assertEqual(produces[0]["to"], "channel:tcp:notify:users")

    def test_dynamic_topic_is_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "svc"
            pkg.mkdir()
            _write(pkg, "dyn.py", """\
                from kafka import KafkaProducer
                def send_dynamic(topic):
                    KafkaProducer.send(topic, b"payload")
            """)
            _, edges, _ = _run(root, "svc/*.py")
            self.assertEqual(
                [e for e in edges if e["type"] == "produces"], []
            )

# ---------------------------------------------------------------------------
# Consumer binding tests
# ---------------------------------------------------------------------------

class EventsConsumerBindingTest(unittest.TestCase):
    """Python subscribe call sites emit ``consumes`` edges."""

    def test_kafka_subscribe_list_emits_consumes_edge(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "svc"
            pkg.mkdir()
            _write(pkg, "sub.py", """\
                from kafka import KafkaConsumer
                def consume_orders():
                    KafkaConsumer.subscribe(["orders.events"])
            """)
            _, edges, discovered = _run(root, "svc/*.py")
            consumes = [e for e in edges if e["type"] == "consumes"]
            self.assertEqual(len(consumes), 1)
            self.assertEqual(consumes[0]["from"], "file:svc/sub.py")
            self.assertEqual(consumes[0]["to"], "channel:kafka:orders.events")
            self.assertEqual(consumes[0]["props"]["confidence"], "inferred")
            self.assertIn("svc/sub.py", discovered)

    def test_redis_subscribe_str_emits_consumes_edge(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "svc"
            pkg.mkdir()
            _write(pkg, "listener.py", """\
                import redis
                def listen():
                    redis.subscribe("notify:users")
            """)
            _, edges, _ = _run(root, "svc/*.py")
            consumes = [e for e in edges if e["type"] == "consumes"]
            self.assertEqual(len(consumes), 1)
            self.assertEqual(consumes[0]["to"], "channel:tcp:notify:users")

    def test_dynamic_subscribe_is_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "svc"
            pkg.mkdir()
            _write(pkg, "dyn.py", """\
                from kafka import KafkaConsumer
                def consume_dynamic(topics):
                    KafkaConsumer.subscribe(topics)
            """)
            _, edges, _ = _run(root, "svc/*.py")
            self.assertEqual(
                [e for e in edges if e["type"] == "consumes"], []
            )

    def test_kafka_subscribe_multi_topic_list(self) -> None:
        """Multiple literal topics in one subscribe yield multiple edges."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "svc"
            pkg.mkdir()
            _write(pkg, "multi.py", """\
                from kafka import KafkaConsumer
                def consume_all():
                    KafkaConsumer.subscribe(["orders.events", "users.created"])
            """)
            _, edges, _ = _run(root, "svc/*.py")
            consumes = [e for e in edges if e["type"] == "consumes"]
            targets = sorted(e["to"] for e in consumes)
            self.assertEqual(
                targets,
                ["channel:kafka:orders.events", "channel:kafka:users.created"],
            )

# ---------------------------------------------------------------------------
# Payload contract linking tests
# ---------------------------------------------------------------------------

class EventsPayloadContractTest(unittest.TestCase):
    """Typed payload params on producer functions emit ``implements`` edges."""

    def test_typed_producer_emits_implements_edge(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "svc"
            pkg.mkdir()
            _write(pkg, "typed.py", """\
                from kafka import KafkaProducer
                from contracts import OrderEvent
                def send_order(event: OrderEvent):
                    KafkaProducer.send("orders.events", event.json())
            """)
            _, edges, _ = _run(root, "svc/*.py")
            implements = [e for e in edges if e["type"] == "implements"]
            self.assertEqual(len(implements), 1)
            self.assertEqual(
                implements[0]["from"], "channel:kafka:orders.events"
            )
            self.assertEqual(implements[0]["to"], "contract:OrderEvent")
            self.assertEqual(
                implements[0]["props"]["confidence"], "inferred"
            )

    def test_primitive_annotation_is_not_linked(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "svc"
            pkg.mkdir()
            _write(pkg, "prim.py", """\
                from kafka import KafkaProducer
                def send_raw(data: bytes):
                    KafkaProducer.send("raw.topic", data)
            """)
            _, edges, _ = _run(root, "svc/*.py")
            implements = [e for e in edges if e["type"] == "implements"]
            self.assertEqual(implements, [])

# ---------------------------------------------------------------------------
# Fragment validation
# ---------------------------------------------------------------------------

class EventsBindingsFragmentValidatesTest(unittest.TestCase):
    """Strategy output must pass contract.validate_fragment."""

    def test_fragment_is_contract_valid(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "svc"
            pkg.mkdir()
            _write(pkg, "pub.py", """\
                from kafka import KafkaProducer
                def send():
                    KafkaProducer.send("orders.events", b"x")
            """)
            _write(pkg, "sub.py", """\
                from kafka import KafkaConsumer
                def consume():
                    KafkaConsumer.subscribe(["orders.events"])
            """)
            nodes, edges, _ = _run(root, "svc/*.py")
            fragment = {"nodes": nodes, "edges": edges, "discovered_from": []}
            errors = validate_fragment(
                fragment,
                source_label="strategy:events_bindings",
                allow_dangling_edges=True,
            )
            self.assertEqual(errors, [], f"unexpected errors: {errors}")

# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------

class EventsBindingsRobustnessTest(unittest.TestCase):
    """Graceful degradation on missing/malformed input."""

    def test_no_async_imports_yields_empty(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "svc"
            pkg.mkdir()
            _write(pkg, "plain.py", "x = 1\n")
            nodes, edges, discovered = _run(root, "svc/*.py")
            self.assertEqual((nodes, edges, discovered), ({}, [], []))

    def test_missing_glob_yields_empty(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            result = extract(root, {}, {})
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])

    def test_file_with_no_calls_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "svc"
            pkg.mkdir()
            _write(pkg, "noop.py", """\
                from kafka import KafkaProducer
                x = KafkaProducer
            """)
            _, edges, discovered = _run(root, "svc/*.py")
            self.assertEqual(edges, [])
            self.assertEqual(discovered, [])

if __name__ == "__main__":
    unittest.main()
