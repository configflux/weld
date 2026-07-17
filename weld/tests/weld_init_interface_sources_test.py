"""Tests for ``wd init`` interface-strategy DETECTION (ADR 0080; the generic-DDS
``dds_idl`` strategy is ADR 0086).

Pins the detection side of auto-wiring: each ``detect_*`` presence signal and
the ``detect_interfaces`` aggregator. The emission (source-entry shape), drift
guard, and end-to-end ``init`` run live in
``weld_init_interface_source_entries_test.py`` -- split so neither file breaches
the 400-line cap."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld._init_interfaces import (  # noqa: E402
    detect_event_compose,
    detect_event_py,
    detect_idl,
    detect_interfaces,
    detect_proto,
    detect_runtime_contract,
)
from weld.init_detect import scan_files  # noqa: E402


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

class DetectProtoTest(unittest.TestCase):
    def test_detects_proto_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "svc.proto").write_text("syntax = \"proto3\";\n")
            self.assertTrue(detect_proto(scan_files(root)))

    def test_no_proto_without_proto_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "main.py").write_text("x = 1\n")
            self.assertFalse(detect_proto(scan_files(root)))


class DetectIdlTest(unittest.TestCase):
    def test_detects_idl_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "sensors.idl").write_text(
                "module S { struct Image { unsigned long w; }; };\n"
            )
            self.assertTrue(detect_idl(scan_files(root)))

    def test_no_idl_without_idl_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "main.py").write_text("x = 1\n")
            self.assertFalse(detect_idl(scan_files(root)))

    def test_msg_file_is_not_idl(self) -> None:
        """ROS2 ``.msg`` interfaces are wired separately, not by dds_idl."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Image.msg").write_text("uint32 width\n")
            self.assertFalse(detect_idl(scan_files(root)))


class DetectEventPyTest(unittest.TestCase):
    def test_detects_kafka_import(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "producer.py").write_text(
                "from kafka import KafkaProducer\n"
            )
            self.assertTrue(detect_event_py(scan_files(root)))

    def test_detects_redis_dotted_import(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pub.py").write_text("from redis.asyncio import Redis\n")
            self.assertTrue(detect_event_py(scan_files(root)))

    def test_detects_aiokafka_import(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.py").write_text("import aiokafka\n")
            self.assertTrue(detect_event_py(scan_files(root)))

    def test_false_friend_module_not_detected(self) -> None:
        """``kafka_helper`` is not the ``kafka`` client root."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "x.py").write_text("import kafka_helper\n")
            self.assertFalse(detect_event_py(scan_files(root)))

    def test_no_event_py_without_client_import(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "plain.py").write_text("import os\nx = 1\n")
            self.assertFalse(detect_event_py(scan_files(root)))


class DetectEventComposeTest(unittest.TestCase):
    def _compose(self, body: str) -> tuple[Path, list[str]]:
        td = tempfile.mkdtemp()
        root = Path(td)
        (root / "docker-compose.yml").write_text(body)
        return root, ["docker-compose.yml"]

    def test_detects_kafka_topic_env(self) -> None:
        root, cf = self._compose(
            "services:\n  api:\n    environment:\n"
            "      KAFKA_ORDERS_TOPIC: orders.placed\n"
        )
        self.assertEqual(detect_event_compose(root, cf), ("docker-compose.yml",))

    def test_detects_celery_queue_and_redis_channel(self) -> None:
        root, cf = self._compose(
            "services:\n  w:\n    environment:\n"
            "      - CELERY_EMAIL_QUEUE=emails\n"
            "      - REDIS_ALERTS_CHANNEL=alerts\n"
        )
        self.assertEqual(detect_event_compose(root, cf), ("docker-compose.yml",))

    def test_plain_compose_not_matched(self) -> None:
        root, cf = self._compose(
            "services:\n  api:\n    image: nginx\n    environment:\n"
            "      DATABASE_URL: postgres://x\n"
        )
        self.assertEqual(detect_event_compose(root, cf), ())


class DetectRuntimeContractTest(unittest.TestCase):
    def test_detects_runtime_contract_md(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "docs").mkdir()
            (root / "docs" / "runtime-contract.md").write_text(
                "## Runtime Summary\n"
            )
            self.assertEqual(
                detect_runtime_contract(root, scan_files(root)),
                "docs/runtime-contract.md",
            )

    def test_no_runtime_contract_without_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "README.md").write_text("# hi\n")
            self.assertIsNone(detect_runtime_contract(root, scan_files(root)))


class DetectInterfacesAggregatorTest(unittest.TestCase):
    """``detect_interfaces`` bundles every presence signal in one pass."""

    def test_bundles_all_signals(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "svc.proto").write_text("syntax = \"proto3\";\n")
            (root / "producer.py").write_text("import aiokafka\n")
            (root / "mqtt_pub.py").write_text(
                "import paho.mqtt.client as mqtt\n"
            )
            (root / "docker-compose.yml").write_text(
                "services:\n  s:\n    environment:\n"
                "      REDIS_ALERTS_CHANNEL: alerts\n"
            )
            (root / "runtime-contract.md").write_text("## Runtime Summary\n")
            (root / "telemetry.idl").write_text(
                "module T { struct Ping { unsigned long seq; }; };\n"
            )
            signals = detect_interfaces(
                root, scan_files(root), ["docker-compose.yml"]
            )
        self.assertTrue(signals.proto)
        self.assertTrue(signals.event_py)
        self.assertTrue(signals.event_mqtt)
        self.assertEqual(signals.event_compose, ("docker-compose.yml",))
        self.assertEqual(signals.runtime_contract, "runtime-contract.md")
        self.assertTrue(signals.idl)

    def test_empty_repo_bundles_no_signals(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "main.py").write_text("x = 1\n")
            signals = detect_interfaces(root, scan_files(root), [])
        self.assertFalse(signals.proto)
        self.assertFalse(signals.event_py)
        self.assertFalse(signals.event_mqtt)
        self.assertEqual(signals.event_compose, ())
        self.assertIsNone(signals.runtime_contract)
        self.assertFalse(signals.idl)


if __name__ == "__main__":
    unittest.main()
