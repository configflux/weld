"""In-repo producer->consumer channel join (discovery post-process).

The events family (``events``/``events_bindings``/``events_mqtt``/
``events_config``) emits, within a single repo:

* a ``channel:<transport>:<topic>`` node per declared async surface
  (``events_shared.channel_node``), and
* directional ``produces`` / ``consumes`` edges from a ``file:`` node to
  that channel node.

Those two halves already meet *at* the shared channel node, but nothing
draws the one-hop ``producer -> consumer`` relationship the polyrepo
story ("module A publishes topic X, module B subscribes to topic X")
asks for. This pass derives it: for every channel node that has at least
one ``produces`` edge and at least one ``consumes`` edge, it emits a
``feeds_into`` edge from each producing file to each consuming file.

``feeds_into`` is the existing data-flow verb in ``VALID_EDGE_TYPES``
(ADR 0004: "stage feeds_into stage"); this pass introduces **no** new
edge-type vocabulary. The derived edge is ``confidence="inferred"``: the
underlying bindings are themselves inferred call-site matches and no
runtime message flow is proven (ADR 0086 static-truth policy). See
ADR 0090.

Determinism (ADR 0011/0012): the pass is *idempotent*. It first strips
any ``feeds_into`` edge it previously stamped (identified by
``props.source_strategy == "channel_join"``) and then re-derives the
whole set from the current graph, so the incremental discover path --
which carries prior edges forward -- produces byte-identical output to a
full discover. Producer/consumer collection and emission both iterate in
sorted order.
"""

from __future__ import annotations

#: Provenance marker stamped on every edge this pass emits. Chosen so the
#: idempotent strip can find *only* this pass's ``feeds_into`` edges and
#: never a ``feeds_into`` emitted by topology overlay or a strategy.
JOIN_SOURCE_STRATEGY = "channel_join"

#: The data-flow verb reused for the derived producer->consumer edge.
JOIN_EDGE_TYPE = "feeds_into"

_PRODUCE_TYPE = "produces"
_CONSUME_TYPE = "consumes"
_CHANNEL_NODE_TYPE = "channel"


def _channel_meta(nodes: dict[str, dict], channel_id: str) -> tuple[str, str]:
    """Return ``(transport, topic)`` for a channel node.

    Prefers the authoritative ``props`` stamped by
    ``events_shared.channel_node`` (``transport`` + ``name``) and falls
    back to parsing the ``channel:<transport>:<topic>`` id so a channel
    minted without those props still yields useful edge metadata.
    """
    props = nodes.get(channel_id, {}).get("props", {})
    transport = props.get("transport")
    topic = props.get("name")
    if transport and topic:
        return str(transport), str(topic)
    # Fallback: ``channel:<transport>:<topic>`` -- split only twice so a
    # topic containing ``:`` is preserved intact.
    parts = channel_id.split(":", 2)
    if len(parts) == 3:
        return parts[1], parts[2]
    return str(transport or ""), str(topic or "")


def _collect(
    nodes: dict[str, dict], edges: list[dict], edge_type: str,
) -> dict[str, set[str]]:
    """Map ``channel_id -> {file_id, ...}`` for one edge direction.

    Only edges whose ``to`` endpoint is a *present* ``channel``-typed node
    and whose ``from`` endpoint is a present node are collected, so the
    pass links exclusively through real shared channel nodes (a dangling
    ``produces``/``consumes`` edge, or a ``produces`` edge into a
    non-channel node such as a ROS2 ``ros_topic``, is ignored).
    """
    by_channel: dict[str, set[str]] = {}
    for edge in edges:
        if edge.get("type") != edge_type:
            continue
        dst = edge.get("to")
        src = edge.get("from")
        dst_node = nodes.get(dst) if isinstance(dst, str) else None
        if dst_node is None or dst_node.get("type") != _CHANNEL_NODE_TYPE:
            continue
        if not isinstance(src, str) or src not in nodes:
            continue
        by_channel.setdefault(dst, set()).add(src)
    return by_channel


def link_producers_consumers(
    nodes: dict[str, dict], edges: list[dict],
) -> None:
    """Emit ``feeds_into`` producer->consumer edges in place.

    Additive and idempotent: existing ``channel_join`` ``feeds_into``
    edges are stripped first, then the full set is re-derived from the
    current ``produces`` / ``consumes`` edges that terminate at present
    ``channel`` nodes. Edges emitted here are deduplicated and dangling-
    swept downstream by ``_clean_and_dedup_edges``; when a producer and a
    consumer share more than one channel, that dedup collapses the pair to
    a single ``feeds_into`` edge (the sorted-first channel supplies props).
    """
    # 1. Idempotent strip -- only this pass's own edges.
    edges[:] = [
        e
        for e in edges
        if not (
            e.get("type") == JOIN_EDGE_TYPE
            and e.get("props", {}).get("source_strategy")
            == JOIN_SOURCE_STRATEGY
        )
    ]

    producers = _collect(nodes, edges, _PRODUCE_TYPE)
    consumers = _collect(nodes, edges, _CONSUME_TYPE)

    # 2. Re-derive. Sorted iteration keeps output a pure function of the
    #    graph, independent of edge/collection insertion order.
    for channel_id in sorted(set(producers) & set(consumers)):
        transport, topic = _channel_meta(nodes, channel_id)
        for producer in sorted(producers[channel_id]):
            for consumer in sorted(consumers[channel_id]):
                if producer == consumer:
                    # A module that both produces and consumes the same
                    # topic does not "feed into" itself.
                    continue
                edges.append({
                    "from": producer,
                    "to": consumer,
                    "type": JOIN_EDGE_TYPE,
                    "props": {
                        "source_strategy": JOIN_SOURCE_STRATEGY,
                        "confidence": "inferred",
                        "channel": channel_id,
                        "transport": transport,
                        "topic": topic,
                    },
                })
