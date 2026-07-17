"""Tests for ``wd init`` interface-strategy EMISSION, drift, and integration
(ADR 0080; the generic-DDS ``dds_idl`` strategy is ADR 0086).

Pins the source-entry shape (``interface_source_entries``), the strategy drift
guard, and an end-to-end ``init`` run over a temp repo carrying every artifact
family. The detection side lives in ``weld_init_interface_sources_test.py`` --
split so neither file breaches the 400-line cap."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld._init_interfaces import (  # noqa: E402
    InterfaceSignals,
    detect_event_compose,
    interface_source_entries,
)
from weld._yaml import parse_yaml  # noqa: E402
from weld.init import init  # noqa: E402


def _entry_field(entry: str, prefix: str) -> str:
    """Pull the quoted/bare value of the first ``- <prefix>:`` line."""
    for line in entry.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            tail = stripped.split(":", 1)[1].strip()
            return tail.split('"', 2)[1] if '"' in tail else tail
    raise AssertionError(f"entry has no {prefix!r} line: {entry}")


def _by_strategy(entries: list[str], strategy: str) -> list[str]:
    """Entries whose ``strategy:`` line is exactly ``strategy`` (not a
    prefix — ``events`` must not match ``events_bindings``)."""
    target = f"strategy: {strategy}"
    return [
        e for e in entries
        if any(line.strip() == target for line in e.splitlines())
    ]


# ---------------------------------------------------------------------------
# Emission
# ---------------------------------------------------------------------------

class InterfaceSourceEntriesTest(unittest.TestCase):
    def test_proto_emits_grpc_proto_and_bindings_with_python(self) -> None:
        signals = InterfaceSignals(proto=True)
        entries = interface_source_entries(signals, ["src/**/*.py"])
        proto = _by_strategy(entries, "grpc_proto")
        bindings = _by_strategy(entries, "grpc_bindings")
        self.assertEqual(len(proto), 1, entries)
        self.assertEqual(len(bindings), 1, entries)
        self.assertEqual(_entry_field(proto[0], "- glob:"), "**/*.proto")
        self.assertEqual(_entry_field(proto[0], "type:"), "rpc")
        self.assertEqual(_entry_field(bindings[0], "- glob:"), "**/*.py")
        self.assertEqual(_entry_field(bindings[0], "type:"), "file")
        self.assertEqual(_entry_field(bindings[0], "proto_glob:"), "**/*.proto")

    def test_proto_without_python_omits_bindings(self) -> None:
        """gRPC bindings are Python-only; no python_globs -> no bindings
        entry, but the proto entry still emits."""
        signals = InterfaceSignals(proto=True)
        entries = interface_source_entries(signals, [])
        self.assertEqual(len(_by_strategy(entries, "grpc_proto")), 1, entries)
        self.assertEqual(_by_strategy(entries, "grpc_bindings"), [])

    def test_event_py_emits_events_callsite_and_bindings(self) -> None:
        signals = InterfaceSignals(event_py=True)
        entries = interface_source_entries(signals, ["src/**/*.py"])
        events = _by_strategy(entries, "events")
        bindings = _by_strategy(entries, "events_bindings")
        self.assertEqual(len(events), 1, entries)
        self.assertEqual(len(bindings), 1, entries)
        self.assertEqual(_entry_field(events[0], "kind:"), "py_callsite")
        self.assertEqual(_entry_field(events[0], "type:"), "channel")
        self.assertEqual(_entry_field(bindings[0], "- glob:"), "**/*.py")

    def test_event_compose_emits_compose_env_entry(self) -> None:
        signals = InterfaceSignals(event_compose=("docker-compose.yml",))
        entries = interface_source_entries(signals, [])
        events = _by_strategy(entries, "events")
        self.assertEqual(len(events), 1, entries)
        self.assertEqual(_entry_field(events[0], "kind:"), "compose_env")
        self.assertEqual(_entry_field(events[0], "- glob:"), "docker-compose.yml")

    def test_runtime_contract_entry(self) -> None:
        signals = InterfaceSignals(runtime_contract="docs/runtime-contract.md")
        entries = interface_source_entries(signals, [])
        rc = _by_strategy(entries, "runtime_contract")
        self.assertEqual(len(rc), 1, entries)
        self.assertEqual(
            _entry_field(rc[0], "- glob:"), "docs/runtime-contract.md"
        )

    def test_idl_emits_dds_idl_entry(self) -> None:
        # ``[]`` python_globs also proves dds_idl is not Python-gated.
        signals = InterfaceSignals(idl=True)
        entries = interface_source_entries(signals, [])
        dds = _by_strategy(entries, "dds_idl")
        self.assertEqual(len(dds), 1, entries)
        self.assertEqual(_entry_field(dds[0], "- glob:"), "**/*.idl")
        self.assertEqual(_entry_field(dds[0], "type:"), "contract")

    def test_no_signals_emits_nothing(self) -> None:
        self.assertEqual(interface_source_entries(InterfaceSignals(), []), [])
        self.assertEqual(
            interface_source_entries(InterfaceSignals(), ["src/**/*.py"]), []
        )


# ---------------------------------------------------------------------------
# Drift guard: init vocab must match the strategies it feeds
# ---------------------------------------------------------------------------

class InterfaceDriftGuardTest(unittest.TestCase):
    def test_event_py_roots_match_strategy(self) -> None:
        from weld._init_interfaces import _EVENT_PY_IMPORT_ROOTS
        from weld.strategies.events_callsite import _PY_IMPORT_ROOTS

        self.assertEqual(
            set(_EVENT_PY_IMPORT_ROOTS), set(_PY_IMPORT_ROOTS),
            "init event-import roots drifted from events_callsite strategy",
        )

    def test_compose_env_parity_with_strategy(self) -> None:
        """Every canonical channel env name the strategy classifies must
        also be detected by the init compose scan, and vice versa."""
        from weld.strategies.events_config import _classify_env_var

        canonical = [
            "KAFKA_ORDERS_TOPIC",
            "KAFKA_TOPIC_ORDERS",
            "CELERY_EMAIL_QUEUE",
            "REDIS_ALERTS_CHANNEL",
        ]
        for name in canonical:
            self.assertIsNotNone(
                _classify_env_var(name), f"strategy missed {name}"
            )
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                (root / "docker-compose.yml").write_text(
                    f"services:\n  s:\n    environment:\n      {name}: v\n"
                )
                self.assertEqual(
                    detect_event_compose(root, ["docker-compose.yml"]),
                    ("docker-compose.yml",),
                    f"init compose scan missed {name}",
                )


# ---------------------------------------------------------------------------
# End-to-end: wd init over a repo with all three families
# ---------------------------------------------------------------------------

class InitInterfaceIntegrationTest(unittest.TestCase):
    def _strategies(self, sources: list[dict]) -> set[str]:
        return {s.get("strategy") for s in sources}

    def test_init_wires_all_interface_strategies(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "orders.proto").write_text(
                "syntax = \"proto3\";\npackage shop;\n"
                "service Orders { rpc Place (Req) returns (Resp); }\n"
                "message Req {}\nmessage Resp {}\n"
            )
            (root / "producer.py").write_text(
                "from kafka import KafkaProducer\n"
                "kafka = KafkaProducer()\n"
                "def go():\n    kafka.send('orders.placed', b'x')\n"
            )
            (root / "docker-compose.yml").write_text(
                "services:\n  api:\n    environment:\n"
                "      KAFKA_ORDERS_TOPIC: orders.placed\n"
            )
            (root / "runtime-contract.md").write_text(
                "## Runtime Summary\n\n"
                "| Boundary | ... | ... | Healthchecks | ... |\n"
                "| `api` | x | y | `GET /healthz` | z |\n"
            )
            (root / "telemetry.idl").write_text(
                "module Telemetry {\n"
                "  @topic\n"
                "  struct Ping { unsigned long seq; };\n"
                "};\n"
            )
            out = root / ".weld" / "discover.yaml"
            self.assertTrue(init(root, out, force=True))
            data = parse_yaml(out.read_text(encoding="utf-8"))
            strategies = self._strategies(data.get("sources", []))

        for expected in (
            "grpc_proto", "grpc_bindings", "events",
            "events_bindings", "runtime_contract", "dds_idl",
        ):
            self.assertIn(
                expected, strategies,
                f"wd init did not wire {expected}: {sorted(strategies)}",
            )

    def test_init_plain_repo_wires_no_interface_strategies(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "main.py").write_text("import os\nx = 1\n")
            out = root / ".weld" / "discover.yaml"
            self.assertTrue(init(root, out, force=True))
            data = parse_yaml(out.read_text(encoding="utf-8"))
            strategies = self._strategies(data.get("sources", []))

        for interface in (
            "grpc_proto", "grpc_bindings", "events",
            "events_bindings", "runtime_contract", "dds_idl",
        ):
            self.assertNotIn(interface, strategies)


if __name__ == "__main__":
    unittest.main()
