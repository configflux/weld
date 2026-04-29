"""Tests for the declared-channel extractor (tracked project).

The ``events`` strategy extracts declared async channels (Kafka topics,
Redis pub/sub channels, Celery queues, etc.) from two conservative
sources:

1. ``compose_env`` — docker-compose YAML files. Scans
   ``services.<svc>.environment`` blocks for env-var names matching
   known channel patterns (``KAFKA_*_TOPIC``, ``CELERY_*_QUEUE``,
   ``REDIS_*_CHANNEL``, etc.) whose values are literal strings.
2. ``py_callsite`` — Python call sites where both the library root and
   the first positional literal are structurally clear in the source
   text (``KafkaProducer.send("topic", ...)``,
   ``redis.publish("chan", ...)``). Dynamic first args -- variables or
   f-strings with substitutions -- are silently dropped per ADR 0018's
   static-truth policy.

Every emitted node is a ``channel`` node stamped with protocol metadata
per ADR 0018 / tracked project:

    protocol="event", surface_kind="pub_sub",
    transport=<kafka|tcp|amqp>, boundary_kind="internal",
    declared_in="<rel-path>"

and a ``contains`` edge links the declaring file node to the channel
node. Producer/consumer linking is explicitly out of scope here; that
lives in tracked project
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
from weld.strategies.events import extract  # noqa: E402

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "events_sample"

def _run_compose(root: Path, glob: str = "docker-compose*.yml") -> tuple[dict, list]:
    result = extract(root, {"kind": "compose_env", "glob": glob}, {})
    return result.nodes, result.edges

def _run_py(root: Path, glob: str = "**/*.py") -> tuple[dict, list]:
    result = extract(root, {"kind": "py_callsite", "glob": glob}, {})
    return result.nodes, result.edges

class ComposeEnvExtractionTest(unittest.TestCase):
    """Declared env-var topics/queues/channels become channel nodes."""

    def test_extracts_kafka_topic_from_mapping_env(self) -> None:
        nodes, _edges = _run_compose(_FIXTURES / "compose")
        channels = [n for n in nodes.values() if n["type"] == "channel"]
        by_name = {n["props"]["name"]: n for n in channels}
        self.assertIn("orders.events", by_name)
        node = by_name["orders.events"]
        self.assertEqual(node["props"]["protocol"], "event")
        self.assertEqual(node["props"]["surface_kind"], "pub_sub")
        self.assertEqual(node["props"]["transport"], "kafka")
        self.assertEqual(node["props"]["boundary_kind"], "internal")
        self.assertEqual(node["props"]["source_strategy"], "events")
        self.assertEqual(node["props"]["authority"], "canonical")
        self.assertEqual(node["props"]["confidence"], "definite")
        self.assertEqual(
            node["props"]["declared_in"], "docker-compose.yml"
        )

    def test_extracts_kafka_topic_from_list_env(self) -> None:
        nodes, _edges = _run_compose(_FIXTURES / "compose")
        by_name = {
            n["props"]["name"]: n
            for n in nodes.values()
            if n["type"] == "channel"
        }
        # KAFKA_USERS_TOPIC=users.created from the list-form environment.
        self.assertIn("users.created", by_name)
        self.assertEqual(by_name["users.created"]["props"]["transport"], "kafka")

    def test_extracts_celery_queue(self) -> None:
        nodes, _edges = _run_compose(_FIXTURES / "compose")
        by_name = {
            n["props"]["name"]: n
            for n in nodes.values()
            if n["type"] == "channel"
        }
        self.assertIn("celery-email", by_name)
        self.assertEqual(
            by_name["celery-email"]["props"]["transport"], "amqp"
        )

    def test_extracts_redis_channel(self) -> None:
        nodes, _edges = _run_compose(_FIXTURES / "compose")
        by_name = {
            n["props"]["name"]: n
            for n in nodes.values()
            if n["type"] == "channel"
        }
        self.assertIn("notify:users", by_name)
        self.assertEqual(
            by_name["notify:users"]["props"]["transport"], "tcp"
        )

    def test_ignores_unrelated_env_vars(self) -> None:
        nodes, _edges = _run_compose(_FIXTURES / "compose")
        names = {
            n["props"]["name"]
            for n in nodes.values()
            if n["type"] == "channel"
        }
        # LOG_LEVEL and DB_URL must not become channels.
        self.assertNotIn("INFO", names)
        self.assertFalse(
            any("postgres" in name for name in names),
            f"postgres URL leaked into channels: {names}",
        )

    def test_compose_emits_contains_edge_from_file(self) -> None:
        nodes, edges = _run_compose(_FIXTURES / "compose")
        channel_ids = {
            nid for nid, n in nodes.items() if n["type"] == "channel"
        }
        file_id = "file:docker-compose.yml"
        contains = [
            e
            for e in edges
            if e["from"] == file_id
            and e["to"] in channel_ids
            and e["type"] == "contains"
        ]
        self.assertGreater(len(contains), 0)
        self.assertEqual(contains[0]["props"]["source_strategy"], "events")

class PyCallsiteExtractionTest(unittest.TestCase):
    """Python call sites with literal first-arg topics become channels."""

    def test_kafka_producer_send_literal_topic(self) -> None:
        nodes, _edges = _run_py(_FIXTURES / "py")
        by_name = {
            n["props"]["name"]: n
            for n in nodes.values()
            if n["type"] == "channel"
        }
        self.assertIn("orders.events", by_name)
        node = by_name["orders.events"]
        self.assertEqual(node["props"]["transport"], "kafka")
        self.assertEqual(node["props"]["protocol"], "event")
        self.assertEqual(node["props"]["declared_in"], "producer.py")

    def test_redis_publish_literal_channel(self) -> None:
        nodes, _edges = _run_py(_FIXTURES / "py")
        by_name = {
            n["props"]["name"]: n
            for n in nodes.values()
            if n["type"] == "channel"
        }
        self.assertIn("notify:users", by_name)
        self.assertEqual(by_name["notify:users"]["props"]["transport"], "tcp")

    def test_dynamic_topic_is_dropped(self) -> None:
        nodes, _edges = _run_py(_FIXTURES / "py")
        # The producer fixture has dynamic_topic() and fstring_topic() --
        # neither should leak a channel node. The only two channels are
        # the literal kafka topic and the literal redis channel.
        channel_names = sorted(
            n["props"]["name"]
            for n in nodes.values()
            if n["type"] == "channel"
        )
        self.assertEqual(channel_names, ["notify:users", "orders.events"])

    def test_py_callsite_emits_contains_edge(self) -> None:
        nodes, edges = _run_py(_FIXTURES / "py")
        channel_ids = {
            nid for nid, n in nodes.items() if n["type"] == "channel"
        }
        file_id = "file:producer.py"
        contains = [
            e
            for e in edges
            if e["from"] == file_id
            and e["to"] in channel_ids
            and e["type"] == "contains"
        ]
        self.assertEqual(len(contains), 2)

class ChannelNodeIdIsStableTest(unittest.TestCase):
    """A channel id is keyed on ``(transport, name)`` so duplicates collapse."""

    def test_duplicate_topic_collapses(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "pkg"
            pkg.mkdir()
            (pkg / "a.py").write_text(textwrap.dedent("""\
                from kafka import KafkaProducer
                def one():
                    KafkaProducer.send("orders", b"x")
            """))
            (pkg / "b.py").write_text(textwrap.dedent("""\
                from kafka import KafkaProducer
                def two():
                    KafkaProducer.send("orders", b"y")
            """))
            nodes, edges = _run_py(root, glob="pkg/*.py")
            channels = [n for n in nodes.values() if n["type"] == "channel"]
            self.assertEqual(len(channels), 1)
            # Both files should declare a contains edge to the same node.
            contains = [
                e for e in edges
                if e["type"] == "contains"
                and e["to"] == "channel:kafka:orders"
            ]
            sources = sorted(e["from"] for e in contains)
            self.assertEqual(
                sources,
                ["file:pkg/a.py", "file:pkg/b.py"],
            )

class EventsFragmentValidatesTest(unittest.TestCase):
    """Strategy output must pass contract.validate_fragment."""

    def test_compose_fragment_is_contract_valid(self) -> None:
        nodes, edges = _run_compose(_FIXTURES / "compose")
        fragment = {
            "nodes": nodes,
            "edges": edges,
            "discovered_from": [],
        }
        errors = validate_fragment(
            fragment,
            source_label="strategy:events",
            allow_dangling_edges=True,
        )
        self.assertEqual(errors, [], f"unexpected errors: {errors}")

    def test_py_fragment_is_contract_valid(self) -> None:
        nodes, edges = _run_py(_FIXTURES / "py")
        fragment = {
            "nodes": nodes,
            "edges": edges,
            "discovered_from": [],
        }
        errors = validate_fragment(
            fragment,
            source_label="strategy:events",
            allow_dangling_edges=True,
        )
        self.assertEqual(errors, [], f"unexpected errors: {errors}")

if __name__ == "__main__":
    unittest.main()
