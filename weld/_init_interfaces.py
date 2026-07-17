"""Interface-strategy detection + source-entry emission for ``wd init``.

Auto-wires the interaction-graph strategies so ``wd init`` surfaces
gRPC/event-channel/runtime-contract/DDS interface data without hand-editing
``discover.yaml`` (ADR 0080; the generic-DDS ``dds_idl`` strategy is ADR
0086):

- ``grpc_proto`` / ``grpc_bindings`` — ``.proto`` services + Python bindings.
- ``events`` / ``events_bindings`` — Kafka/Celery/Redis channels from
  docker-compose env vars and Python call sites.
- ``events_mqtt`` — MQTT channels from paho.mqtt / asyncio-mqtt
  publish/subscribe call sites.
- ``runtime_contract`` — healthcheck rpcs from ``runtime-contract.md``.
- ``dds_idl`` — generic (non-ROS2) DDS ``.idl`` data contracts, enums, and
  pub/sub topic channels (CycloneDDS / FastDDS). ROS2 interface files
  (``.msg`` / ``.srv`` / ``.action``) are wired separately in
  :mod:`weld._init_ros2`.

Mirrors the ROS2 (:mod:`weld._init_ros2`) and C#/C++ init helpers: one
cohesive module owning both the presence detectors and the YAML
source-entry builder. ``weld.init`` builds the entries here and extends the
``code`` bucket, exactly as it does with ``cpp_bs``. ``weld.init_detect`` is
at its line-count cap, so this detection logic lives here rather than there.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from weld._init_framework_scan import _MAX_FILES_PER_LANG
from weld._init_framework_sources import _source_entry

# Python client-library import roots the ``events`` py_callsite +
# ``events_bindings`` strategies can extract from. Kept in lockstep with
# ``weld.strategies.events_callsite._PY_IMPORT_ROOTS`` (drift-guarded by
# ``weld_init_interface_sources_test.InterfaceDriftGuardTest``).
_EVENT_PY_IMPORT_ROOTS: tuple[str, ...] = ("kafka", "redis", "aiokafka")

# MQTT client-library import roots the ``events_mqtt`` strategy extracts
# from (paho.mqtt / asyncio-mqtt / aiomqtt). Kept in lockstep with
# ``weld.strategies.events_mqtt._IMPORT_ROOTS`` (drift-guarded by
# ``weld_init_interface_sources_test.InterfaceDriftGuardTest``).
_MQTT_PY_IMPORT_ROOTS: tuple[str, ...] = ("paho", "asyncio_mqtt", "aiomqtt")

# Compose channel env-var name shapes, mirroring
# ``weld.strategies.events_config._ENV_RULES``. Presence of any in a
# compose file wires the ``events`` compose_env half.
_EVENT_ENV_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"KAFKA_[A-Z0-9_]+_TOPIC"),
    re.compile(r"KAFKA_TOPIC_[A-Z0-9_]+"),
    re.compile(r"CELERY_[A-Z0-9_]+_QUEUE"),
    re.compile(r"REDIS_[A-Z0-9_]+_CHANNEL"),
)

_PROTO_GLOB = "**/*.proto"
_IDL_GLOB = "**/*.idl"
_PY_GLOB = "**/*.py"
_RUNTIME_CONTRACT_NAME = "runtime-contract.md"


@dataclass(frozen=True)
class InterfaceSignals:
    """Presence signals for the interface strategies detected at ``wd init``.

    ``event_compose`` is the sorted tuple of compose files that declare a
    channel env var; ``runtime_contract`` is the relative path of a
    detected ``runtime-contract.md`` (or ``None``). ``event_mqtt`` is set
    when a ``.py`` file imports an MQTT client library. ``idl`` is set when
    any ``.idl`` file is present (generic DDS data-definition files).
    """

    proto: bool = False
    event_py: bool = False
    event_mqtt: bool = False
    event_compose: tuple[str, ...] = ()
    runtime_contract: str | None = None
    idl: bool = False


def detect_proto(files: list[Path]) -> bool:
    """True when any ``.proto`` file is present (suffix check, no reads)."""
    return any(f.suffix == ".proto" for f in files)


def detect_idl(files: list[Path]) -> bool:
    """True when any ``.idl`` file is present (suffix check, no reads).

    Mirrors :func:`detect_proto`: presence of a generic DDS ``.idl``
    data-definition file (CycloneDDS / FastDDS) wires the ``dds_idl``
    strategy. ROS2 interface files (``.msg`` / ``.srv`` / ``.action``) use
    a different extension and are wired separately by
    :mod:`weld._init_ros2`, so there is no overlap.
    """
    return any(f.suffix == ".idl" for f in files)


def _module_root(line: str) -> str | None:
    """Return the dotted-path root of a Python ``import``/``from`` line.

    ``from redis.asyncio import Redis`` -> ``redis``; ``import kafka`` ->
    ``kafka``; ``import kafka_helper`` -> ``kafka_helper`` (no false match).
    Non-import lines return ``None``.
    """
    for kw in ("import ", "from "):
        if line.startswith(kw):
            token = line[len(kw):].lstrip()
            for i, ch in enumerate(token):
                if ch in " \t.,;":
                    return token[:i]
            return token or None
    return None


def _has_import_root(text: str, roots: tuple[str, ...]) -> bool:
    for raw in text.splitlines():
        if _module_root(raw.strip()) in roots:
            return True
    return False


def _detect_py_import(files: list[Path], roots: tuple[str, ...]) -> bool:
    """Bounded scan: True when a ``.py`` file imports a top-level *roots* name.

    Reads at most ``_MAX_FILES_PER_LANG`` Python files (ADR 0027) and
    returns on the first hit, so ``wd init`` stays fast on large repos.
    """
    scanned = 0
    for f in files:
        if f.suffix != ".py":
            continue
        if scanned >= _MAX_FILES_PER_LANG:
            break
        scanned += 1
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if _has_import_root(text, roots):
            return True
    return False


def detect_event_py(files: list[Path]) -> bool:
    """True when a ``.py`` file imports a known Kafka/Redis async client."""
    return _detect_py_import(files, _EVENT_PY_IMPORT_ROOTS)


def detect_event_mqtt(files: list[Path]) -> bool:
    """True when a ``.py`` file imports an MQTT client (paho / asyncio-mqtt)."""
    return _detect_py_import(files, _MQTT_PY_IMPORT_ROOTS)


def detect_event_compose(root: Path, compose_files: list[str]) -> tuple[str, ...]:
    """Return the compose files (sorted) that declare a channel env var."""
    matched: list[str] = []
    for cf in compose_files:
        try:
            text = (root / cf).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if any(pat.search(text) for pat in _EVENT_ENV_PATTERNS):
            matched.append(cf)
    return tuple(sorted(matched))


def detect_runtime_contract(root: Path, files: list[Path]) -> str | None:
    """Return the POSIX relative path of a ``runtime-contract.md``, or None.

    Deterministic: when several are present the lexicographically-first
    path wins.
    """
    hits = sorted(
        f.relative_to(root).as_posix()
        for f in files
        if f.name == _RUNTIME_CONTRACT_NAME
    )
    return hits[0] if hits else None


def detect_interfaces(
    root: Path, files: list[Path], compose_files: list[str],
) -> InterfaceSignals:
    """Single entry point: presence signals for the interface strategies."""
    return InterfaceSignals(
        proto=detect_proto(files),
        event_py=detect_event_py(files),
        event_mqtt=detect_event_mqtt(files),
        event_compose=detect_event_compose(root, compose_files),
        runtime_contract=detect_runtime_contract(root, files),
        idl=detect_idl(files),
    )


def interface_source_entries(
    signals: InterfaceSignals, python_globs: list[str],
) -> list[str]:
    """Build discover.yaml ``code``-bucket entries for detected interfaces.

    Python-scanning entries (``grpc_bindings``, ``events`` py_callsite,
    ``events_bindings``) use a repo-wide ``**/*.py`` glob because
    bindings/call-sites are not confined to a conventional directory; the
    strategies are conservative, so a broad glob is a safe default. gRPC
    bindings are gated on Python being present.
    """
    entries: list[str] = []
    has_python = bool(python_globs)

    if signals.proto:
        entries.append(_source_entry(
            _PROTO_GLOB, "rpc", "grpc_proto",
            comment="gRPC proto services/messages/enums",
        ))
        if has_python:
            entries.append(_source_entry(
                _PY_GLOB, "file", "grpc_bindings",
                comment="gRPC server/client bindings (Python)",
                extra={"proto_glob": f'"{_PROTO_GLOB}"'},
            ))

    if signals.event_py:
        entries.append(_source_entry(
            _PY_GLOB, "channel", "events",
            comment="Async event channels (Python producers/consumers)",
            extra={"kind": "py_callsite"},
        ))
        entries.append(_source_entry(
            _PY_GLOB, "file", "events_bindings",
            comment="Async channel producer/consumer bindings (Python)",
        ))

    if signals.event_mqtt:
        entries.append(_source_entry(
            _PY_GLOB, "channel", "events_mqtt",
            comment="MQTT channels (paho.mqtt / asyncio-mqtt publish/subscribe)",
        ))

    for cf in signals.event_compose:
        entries.append(_source_entry(
            cf, "channel", "events",
            comment="Async event channels (compose env vars)",
            extra={"kind": "compose_env"},
        ))

    if signals.runtime_contract:
        entries.append(_source_entry(
            signals.runtime_contract, "rpc", "runtime_contract",
            comment="Runtime-contract healthchecks + boundary linkage",
        ))

    if signals.idl:
        # ``type: contract`` matches the canonical hand-written entry in
        # ``weld/docs/strategy-cookbook.md`` (the strategy emits contract,
        # enum, and channel nodes; ``contract`` is the declared primary).
        entries.append(_source_entry(
            _IDL_GLOB, "contract", "dds_idl",
            comment="Generic DDS .idl data contracts + pub/sub topic channels",
        ))

    return entries
