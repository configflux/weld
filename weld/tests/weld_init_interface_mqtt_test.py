"""Tests for ``wd init`` MQTT interface-strategy auto-wiring (ADR 0080).

Companion to ``weld_init_interface_sources_test`` (which covers the
gRPC / Kafka-Redis-events / compose / runtime-contract detectors). This
module isolates the ``events_mqtt`` slice: the paho.mqtt / asyncio-mqtt
import detector, the source-entry emission, and the drift guard that
keeps the init import-root vocabulary in lockstep with the strategy.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


from weld._init_interfaces import (  # noqa: E402
    InterfaceSignals,
    detect_event_mqtt,
    interface_source_entries,
)
from weld.init_detect import scan_files  # noqa: E402


def _entry_field(entry: str, prefix: str) -> str:
    """Pull the quoted/bare value of the first ``- <prefix>:`` line."""
    for line in entry.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            tail = stripped.split(":", 1)[1].strip()
            return tail.split('"', 2)[1] if '"' in tail else tail
    raise AssertionError(f"entry has no {prefix!r} line: {entry}")


def _by_strategy(entries: list[str], strategy: str) -> list[str]:
    """Entries whose ``strategy:`` line is exactly ``strategy``."""
    target = f"strategy: {strategy}"
    return [
        e for e in entries
        if any(line.strip() == target for line in e.splitlines())
    ]


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

class DetectEventMqttTest(unittest.TestCase):
    def test_detects_mqtt_client_imports(self) -> None:
        for imp in (
            "import paho.mqtt.client as mqtt",
            "from asyncio_mqtt import Client",
            "import aiomqtt",
        ):
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                (root / "m.py").write_text(imp + "\n")
                self.assertTrue(detect_event_mqtt(scan_files(root)), imp)

    def test_no_mqtt_without_client_import(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "plain.py").write_text("import os\nx = 1\n")
            self.assertFalse(detect_event_mqtt(scan_files(root)))

    def test_kafka_import_is_not_mqtt(self) -> None:
        """The MQTT scan must not fire on the kafka/redis event roots."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "k.py").write_text("from kafka import KafkaProducer\n")
            self.assertFalse(detect_event_mqtt(scan_files(root)))


# ---------------------------------------------------------------------------
# Emission
# ---------------------------------------------------------------------------

class MqttSourceEntryTest(unittest.TestCase):
    def test_event_mqtt_emits_events_mqtt_entry(self) -> None:
        signals = InterfaceSignals(event_mqtt=True)
        entries = interface_source_entries(signals, ["src/**/*.py"])
        mqtt = _by_strategy(entries, "events_mqtt")
        self.assertEqual(len(mqtt), 1, entries)
        self.assertEqual(_entry_field(mqtt[0], "- glob:"), "**/*.py")
        self.assertEqual(_entry_field(mqtt[0], "type:"), "channel")

    def test_event_py_does_not_emit_events_mqtt(self) -> None:
        """Kafka/Redis detection must not wire the MQTT strategy."""
        signals = InterfaceSignals(event_py=True)
        entries = interface_source_entries(signals, ["src/**/*.py"])
        self.assertEqual(_by_strategy(entries, "events_mqtt"), [])

    def test_no_signals_emits_no_mqtt(self) -> None:
        self.assertEqual(
            _by_strategy(interface_source_entries(InterfaceSignals(), []),
                         "events_mqtt"),
            [],
        )


# ---------------------------------------------------------------------------
# Drift guard: init vocab must match the strategy it feeds
# ---------------------------------------------------------------------------

class MqttDriftGuardTest(unittest.TestCase):
    def test_event_mqtt_roots_match_strategy(self) -> None:
        from weld._init_interfaces import _MQTT_PY_IMPORT_ROOTS
        from weld.strategies.events_mqtt import _IMPORT_ROOTS

        self.assertEqual(
            set(_MQTT_PY_IMPORT_ROOTS), set(_IMPORT_ROOTS),
            "init mqtt-import roots drifted from events_mqtt strategy",
        )


if __name__ == "__main__":
    unittest.main()
