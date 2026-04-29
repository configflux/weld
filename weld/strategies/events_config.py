"""Declared-channel extraction from config surfaces (tracked project).

Config-first half of the ``events`` strategy. Walks declared YAML/env
surfaces (today: ``docker-compose*.yml``) for channel env-var entries
whose value is a bare literal.

The callsite half lives in :mod:`weld.strategies.events_callsite`; the
facade in :mod:`weld.strategies.events` dispatches between the two based
on ``source["kind"]``. See that module docstring for the full policy.
"""

from __future__ import annotations

import re
from pathlib import Path

from weld.strategies._helpers import filter_glob_results
from weld.strategies.events_shared import (
    channel_id,
    channel_node,
    contains_edge,
)

# ---------------------------------------------------------------------------
# Compose env-var patterns.
#
# Each rule is ``(regex, transport)``. The regex is matched against the
# full env-var name. Only the first matching rule wins. Keep these
# patterns narrow on purpose: any ambiguity means the declaration is
# dropped rather than mis-classified.
# ---------------------------------------------------------------------------
_TRANSPORT_KAFKA = "kafka"
_TRANSPORT_AMQP = "amqp"  # Celery default broker family.
_TRANSPORT_TCP = "tcp"    # Redis pub/sub and other TCP-native buses.

_ENV_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^KAFKA_[A-Z0-9_]+_TOPIC$"), _TRANSPORT_KAFKA),
    (re.compile(r"^KAFKA_TOPIC_[A-Z0-9_]+$"), _TRANSPORT_KAFKA),
    (re.compile(r"^CELERY_[A-Z0-9_]+_QUEUE$"), _TRANSPORT_AMQP),
    (re.compile(r"^REDIS_[A-Z0-9_]+_CHANNEL$"), _TRANSPORT_TCP),
)

def _classify_env_var(name: str) -> str | None:
    """Return the transport for a matching env-var name, or None."""
    for pattern, transport in _ENV_RULES:
        if pattern.match(name):
            return transport
    return None

# ---------------------------------------------------------------------------
# docker-compose YAML line walker
#
# We parse line-by-line rather than importing PyYAML: the weld helpers
# module intentionally avoids a hard PyYAML dep, and the compose
# strategy next door uses the same approach. We only need to recognise
# four shapes:
#
#   services:
#     svc:
#       environment:
#         KAFKA_FOO_TOPIC: "bar"        # mapping, quoted
#         KAFKA_FOO_TOPIC: bar          # mapping, bare
#         - KAFKA_FOO_TOPIC=bar         # list form
#
# Anything else (anchors, merges, env-file references, interpolated
# values like ``${TOPIC}``) is outside the static-truth window and is
# silently skipped.
# ---------------------------------------------------------------------------

_MAPPING_RE = re.compile(
    r"^\s*(?P<key>[A-Z][A-Z0-9_]*)\s*:\s*(?P<val>.+?)\s*$"
)
_LIST_RE = re.compile(
    r"^\s*-\s*(?P<key>[A-Z][A-Z0-9_]*)\s*=\s*(?P<val>.+?)\s*$"
)

def _strip_quotes(value: str) -> str | None:
    """Return the literal string value, or None if not a bare literal."""
    v = value.strip()
    # Drop trailing inline comments.
    if " #" in v:
        v = v.split(" #", 1)[0].rstrip()
    if not v:
        return None
    if v.startswith('"') and v.endswith('"') and len(v) >= 2:
        v = v[1:-1]
    elif v.startswith("'") and v.endswith("'") and len(v) >= 2:
        v = v[1:-1]
    if "${" in v or "$(" in v:
        return None
    return v

def _iter_env_declarations(text: str) -> list[tuple[str, str]]:
    """Walk *text* and yield ``(env_var, literal)`` under services.*.environment.

    Block membership is tracked by indent depth. We enter an environment
    block on seeing ``environment:`` and leave it as soon as the
    indentation drops back to or below the block's own column.
    """
    found: list[tuple[str, str]] = []
    in_services = False
    services_indent = -1
    in_env = False
    env_indent = -1

    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())

        if not in_services:
            if stripped == "services:":
                in_services = True
                services_indent = indent
            continue

        if indent <= services_indent and stripped != "services:":
            in_services = False
            in_env = False
            continue

        if in_env:
            if indent <= env_indent:
                in_env = False
                # Fall through: this line may open a new environment block.
            else:
                list_match = _LIST_RE.match(line)
                if list_match:
                    val = _strip_quotes(list_match.group("val"))
                    if val is not None:
                        found.append((list_match.group("key"), val))
                    continue
                map_match = _MAPPING_RE.match(line)
                if map_match:
                    val = _strip_quotes(map_match.group("val"))
                    if val is not None:
                        found.append((map_match.group("key"), val))
                    continue
                continue

        if stripped == "environment:":
            in_env = True
            env_indent = indent

    return found

def extract_compose_env(
    root: Path, pattern: str
) -> tuple[dict[str, dict], list[dict], list[str]]:
    """Extract declared channel env vars from docker-compose files."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    parent = (root / pattern).parent
    if not parent.is_dir():
        parent = root
    candidates = filter_glob_results(
        root, sorted(parent.glob(Path(pattern).name))
    )

    for cf in candidates:
        if not cf.is_file():
            continue
        try:
            text = cf.read_text(encoding="utf-8")
        except OSError:
            continue
        rel_path = str(cf.relative_to(root))
        declarations = _iter_env_declarations(text)
        matched = False
        for key, value in declarations:
            transport = _classify_env_var(key)
            if transport is None or not value:
                continue
            nid = channel_id(transport, value)
            nodes[nid] = channel_node(
                transport=transport, name=value, rel_path=rel_path
            )
            edges.append(contains_edge(f"file:{rel_path}", nid))
            matched = True
        if matched:
            discovered_from.append(rel_path)

    return nodes, edges, discovered_from
