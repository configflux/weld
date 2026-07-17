"""Declared-channel extraction from Python call sites (tracked project).

Code half of the ``events`` strategy. Walks Python files for calls
shaped ``<Root>.<verb>("literal", ...)`` where ``<Root>`` is a known
async client identifier and ``<verb>`` is a known publish *or* subscribe
verb. As with :mod:`weld.strategies.http_client`, resolving assigned
instances or attribute chains is out of scope; per ADR 0086, omission is
preferred over guesswork.

The ``channel:<transport>:<topic>`` node is minted at both producer
(``send`` / ``produce`` / ``publish``) and consumer (``subscribe``)
sites -- symmetric with :mod:`weld.strategies.events_mqtt`. Minting at
consume sites keeps the channel node present in a *consumer-only* repo, so
the ``consumes`` edge that :mod:`weld.strategies.events_bindings` emits
there resolves rather than dangling (and being swept by the post-process
dangling-edge pass). That is what lets ADR 0090's in-repo ``feeds_into``
join and the ``channel_binding`` cross-repo resolver match a kafka/redis
consumer that has no local producer or config declaration. Consumer topics
use the same single-or-list literal extraction as ``events_bindings`` (the
edge emitter), so the node minter and the edge emitter can never disagree
on which topics a subscribe call declares.

The config half lives in :mod:`weld.strategies.events_config`; the
facade in :mod:`weld.strategies.events` dispatches between the two.
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
    literal_str_or_list_arg,
)
from weld.strategies.events_shared import (
    channel_id,
    channel_node,
    contains_edge,
    file_node_id,
)

_TRANSPORT_KAFKA = "kafka"
_TRANSPORT_TCP = "tcp"

# ---------------------------------------------------------------------------
# Call-site vocabulary.
#
# A rule fires when the receiver Name is in ``roots`` and the attribute
# being called is in ``verbs``. Everything else -- assigned instances,
# deep attribute chains -- is left alone. Producer and consumer rules
# mirror :mod:`weld.strategies.events_bindings` (the edge emitter), so the
# minted channel nodes and the produces/consumes edges stay in lockstep.
# ---------------------------------------------------------------------------
_PRODUCER_RULES: tuple[tuple[frozenset[str], frozenset[str], str], ...] = (
    (
        frozenset(["KafkaProducer", "kafka"]),
        frozenset(["send", "produce", "send_and_wait"]),
        _TRANSPORT_KAFKA,
    ),
    (
        frozenset(["redis"]),
        frozenset(["publish"]),
        _TRANSPORT_TCP,
    ),
)

_CONSUMER_RULES: tuple[tuple[frozenset[str], frozenset[str], str], ...] = (
    (
        frozenset(["KafkaConsumer", "kafka"]),
        frozenset(["subscribe"]),
        _TRANSPORT_KAFKA,
    ),
    (
        frozenset(["redis"]),
        frozenset(["subscribe"]),
        _TRANSPORT_TCP,
    ),
)

#: Library root names that indicate an async client import. Cheap
#: pre-filter so we only AST-walk files that could possibly match.
_PY_IMPORT_ROOTS: frozenset[str] = frozenset(["kafka", "redis", "aiokafka"])

def _collect_calls(tree: ast.Module) -> list[tuple[str, str]]:
    """Walk *tree* and return ``(transport, topic)`` for static call sites.

    Producer sites (``send`` / ``produce`` / ``publish``) contribute their
    single literal topic; consumer sites (``subscribe``) contribute each
    literal topic in the single- or list-form argument. Both feed the same
    node + ``contains``-edge emission, so the channel node is minted
    symmetrically at publish and subscribe sites. Producer and consumer
    verb sets are disjoint, so each call classifies at most once.
    """
    found: list[tuple[str, str]] = []
    for node in iter_call_nodes(tree):
        transport = classify_receiver_verb(node, _PRODUCER_RULES)
        if transport is not None:
            name = literal_first_arg(node)
            if name:
                found.append((transport, name))
            continue
        transport = classify_receiver_verb(node, _CONSUMER_RULES)
        if transport is not None:
            topics = literal_str_or_list_arg(node)
            if topics:
                found.extend((transport, topic) for topic in topics)
    return found

def extract_py_callsite(
    root: Path, pattern: str
) -> tuple[dict[str, dict], list[dict], list[str]]:
    """Extract declared channels from Python publish/subscribe call sites."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    for rel_path, tree in iter_python_asts(root, pattern):
        if not file_imports_root(tree, _PY_IMPORT_ROOTS):
            continue
        calls = _collect_calls(tree)
        if not calls:
            continue

        discovered_from.append(rel_path)
        file_id = file_node_id(rel_path)

        for transport, name in calls:
            nid = channel_id(transport, name)
            nodes[nid] = channel_node(
                transport=transport, name=name, rel_path=rel_path
            )
            edges.append(contains_edge(file_id, nid))

    return nodes, edges, discovered_from
