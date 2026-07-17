"""Strategy: MQTT channel producer/consumer extraction (tracked project).

Recognizes paho.mqtt / asyncio-mqtt (aiomqtt) ``publish()`` /
``subscribe()`` call sites and emits ``channel:mqtt:<topic>`` nodes with
directional ``produces`` / ``consumes`` edges (``transport="mqtt"``).
It fills the gap where ``mqtt`` is a valid ``event`` transport in
``weld.contract.PROTOCOL_TRANSPORT_COMPATIBILITY`` but no strategy
emitted it.

Modeled on :mod:`weld.strategies.events_bindings` and
:mod:`weld.strategies.events_callsite`, sharing the call-site AST-match
primitives in :mod:`weld.strategies._ast_calls`. Per ADR 0086's
static-truth policy, detection is purely structural -- both the receiver
and the topic string must be clear in the source text:

- Producer: ``<Root>.publish("literal", ...)`` where ``<Root>`` is an
  idiomatic MQTT client handle (``client``, ``mqtt_client``, ``mqttc``).
  Emits a ``channel:mqtt:<topic>`` node plus a ``produces`` edge from
  ``file:<rel-path>`` to the channel.

- Consumer: ``<Root>.subscribe("literal")``. Emits a
  ``channel:mqtt:<topic>`` node plus a ``consumes`` edge.

A cheap import pre-filter restricts the AST walk to files importing an
MQTT client library (``paho`` / ``asyncio_mqtt`` / ``aiomqtt``), so the
common ``client`` receiver name only fires where an MQTT client is in
play. Channel nodes reuse :func:`weld.strategies.events_shared.channel_node`
(``confidence="definite"``); the directional edges carry
``source_strategy="events_mqtt"`` / ``confidence="inferred"`` -- the same
node-definite / edge-inferred split the rest of the events family uses.

Out of scope (ADR 0086): assigned-instance / attribute-chain receivers
(``c = mqtt.Client(); c.publish(...)`` with ``c`` unrecognized), dynamic
(non-literal) topics, paho's module-level ``publish.single`` /
``publish.multiple`` helpers, and QoS-tuple / topic-list ``subscribe``
forms -- all dropped rather than guessed.
"""

from __future__ import annotations

import ast
from pathlib import Path

from weld.strategies._ast_calls import (
    classify_receiver_verb,
    file_imports_root,
    iter_call_nodes,
    iter_python_asts,
    literal_first_arg,
)
from weld.strategies._helpers import StrategyResult
from weld.strategies.events_shared import channel_id, channel_node, file_node_id

_TRANSPORT_MQTT = "mqtt"

#: Idiomatic MQTT client-handle receiver names. A bare ``Name`` receiver
#: matching one of these plus a publish/subscribe verb is treated as an
#: MQTT call site (the import pre-filter keeps this from over-firing).
_CLIENT_ROOTS: frozenset[str] = frozenset(["client", "mqtt_client", "mqttc"])

#: (root_names, publish_verbs, transport)
_PRODUCER_RULES: tuple[tuple[frozenset[str], frozenset[str], str], ...] = (
    (_CLIENT_ROOTS, frozenset(["publish"]), _TRANSPORT_MQTT),
)

#: (root_names, subscribe_verbs, transport)
_CONSUMER_RULES: tuple[tuple[frozenset[str], frozenset[str], str], ...] = (
    (_CLIENT_ROOTS, frozenset(["subscribe"]), _TRANSPORT_MQTT),
)

#: MQTT client-library import roots for the cheap pre-filter. Kept in
#: lockstep with ``weld._init_interfaces._MQTT_PY_IMPORT_ROOTS``
#: (drift-guarded by ``weld_init_interface_sources_test``).
_IMPORT_ROOTS: frozenset[str] = frozenset(["paho", "asyncio_mqtt", "aiomqtt"])


def _edge(src: str, dst: str, etype: str) -> dict:
    return {
        "from": src,
        "to": dst,
        "type": etype,
        "props": {
            "source_strategy": "events_mqtt",
            "confidence": "inferred",
        },
    }


def _emit(
    node: ast.Call,
    rules: tuple[tuple[frozenset[str], frozenset[str], str], ...],
    etype: str,
    rel_path: str,
    file_id: str,
    nodes: dict[str, dict],
    edges: list[dict],
) -> bool:
    """Emit a channel node + directional edge for a matching call, if literal.

    Returns True when a matching call site with a literal topic produced
    output. Non-matching receivers/verbs and dynamic topics return False.
    """
    transport = classify_receiver_verb(node, rules)
    if transport is None:
        return False
    topic = literal_first_arg(node)
    if not topic:
        return False
    cid = channel_id(transport, topic)
    nodes[cid] = channel_node(
        transport=transport, name=topic, rel_path=rel_path
    )
    edges.append(_edge(file_id, cid, etype))
    return True


def _process_file(
    tree: ast.Module,
    rel_path: str,
    nodes: dict[str, dict],
    edges: list[dict],
) -> bool:
    """Process one parsed module. Returns True if any output was emitted."""
    file_id = file_node_id(rel_path)
    emitted = False
    for node in iter_call_nodes(tree):
        if _emit(
            node, _PRODUCER_RULES, "produces", rel_path, file_id, nodes, edges
        ):
            emitted = True
            continue
        if _emit(
            node, _CONSUMER_RULES, "consumes", rel_path, file_id, nodes, edges
        ):
            emitted = True
    return emitted


def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Emit ``channel:mqtt:<topic>`` nodes + produces/consumes edges."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source.get("glob")
    if not pattern:
        return StrategyResult(nodes, edges, discovered_from)

    for rel_path, tree in iter_python_asts(root, pattern):
        if not file_imports_root(tree, _IMPORT_ROOTS):
            continue
        if _process_file(tree, rel_path, nodes, edges):
            discovered_from.append(rel_path)

    return StrategyResult(nodes, edges, discovered_from)
