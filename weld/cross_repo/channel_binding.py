"""Cross-repo resolver: channel producer/consumer binding.

Matches event-channel producers in one child repository to consumers in
another. Within a single repo the ``feeds_into`` join (ADR 0090, run at
discover time) already links a producer file to a consumer file that
share a ``channel:<transport>:<topic>`` node; this resolver is the
federated analogue. It fills the polyrepo story "service A publishes
topic X, service B subscribes to topic X" that the single-repo pass
cannot see because each child is discovered in isolation.

The matching key is the **channel node id** minted by
``weld.strategies.events_shared.channel_id`` -- ``channel:<transport>:
<topic>`` -- which is identical across children by construction, so an
exact id match is the shared pub/sub contract. For every producer in
child A and consumer in child B (A != B) that reference the same channel
id, the resolver emits a ``cross_repo:channel_flow`` edge whose
``from_id`` is the namespaced producing file in A and ``to_id`` is the
namespaced consuming file in B.

Design decisions (mirroring ``grpc_service_binding`` / ``service_graph``):

* Only ``present`` children are inspected; the framework filters the rest
  out of :attr:`ResolverContext.children`.
* Output is deterministic: edges are sorted by a canonical key so
  identical inputs produce byte-identical output across runs.
* A producer whose topic no sibling consumes (and vice versa) produces no
  edge and no error.
* ``confidence`` is ``inferred``: the underlying produces/consumes
  bindings are themselves inferred call-site matches and no runtime
  message flow is proven (ADR 0086 static-truth policy). The resolver
  leaks nothing across the child boundary beyond the join edge itself --
  the producing/consuming file ids and the shared topic key both sides
  already declare.
"""

from __future__ import annotations

import json
from typing import Any

from weld.cross_repo.base import (
    CrossRepoEdge,
    CrossRepoResolver,
    ResolverContext,
    _iter_nodes,
    register_resolver,
)
from weld.workspace import UNIT_SEPARATOR

_PRODUCE_TYPE = "produces"
_CONSUME_TYPE = "consumes"
_CHANNEL_NODE_TYPE = "channel"


def _channel_meta(graph: Any) -> dict[str, tuple[str, str]]:
    """Return ``{channel_id: (transport, topic)}`` for the child's channels.

    Prefers the ``transport`` / ``name`` props stamped by
    ``events_shared.channel_node`` and falls back to parsing the
    ``channel:<transport>:<topic>`` id (split twice so a topic containing
    ``:`` survives). Sorted iteration keeps the map's construction stable.
    """
    meta: dict[str, tuple[str, str]] = {}
    for node_id, node in sorted(_iter_nodes(graph)):
        if node.get("type") != _CHANNEL_NODE_TYPE:
            continue
        props = node.get("props", {})
        transport = props.get("transport")
        topic = props.get("name")
        if not (transport and topic):
            parts = node_id.split(":", 2)
            if len(parts) == 3:
                transport, topic = transport or parts[1], topic or parts[2]
        meta[node_id] = (str(transport or ""), str(topic or ""))
    return meta


def _extract_endpoints(
    child_name: str, graph: Any, edge_type: str, channels: dict[str, tuple[str, str]],
) -> dict[str, list[str]]:
    """Return ``{channel_id: [namespaced_file_id, ...]}`` for one direction.

    Scans edges of ``edge_type`` (``produces``/``consumes``) whose ``to``
    endpoint is a channel node present in ``channels``; the ``from`` side
    is namespaced with ``child_name``. Endpoints are collected in sorted
    order so the per-channel lists are deterministic.
    """
    by_channel: dict[str, set[str]] = {}
    edges = getattr(graph, "_data", {}).get("edges", [])
    for edge in edges:
        if edge.get("type") != edge_type:
            continue
        dst = edge.get("to")
        src = edge.get("from")
        if dst not in channels or not isinstance(src, str):
            continue
        namespaced = f"{child_name}{UNIT_SEPARATOR}{src}"
        by_channel.setdefault(dst, set()).add(namespaced)
    return {cid: sorted(ids) for cid, ids in by_channel.items()}


def _build_edges(
    producers: dict[str, dict[str, list[str]]],
    consumers: dict[str, dict[str, list[str]]],
    channel_meta: dict[str, tuple[str, str]],
) -> list[CrossRepoEdge]:
    """Cross-join producers to consumers on a shared channel across children.

    ``producers``/``consumers`` map ``child_name -> {channel_id -> [file_ids]}``.
    For each producing child and channel, every *other* child that consumes
    the same channel yields one edge per (producer, consumer) file pair.
    """
    edges: list[CrossRepoEdge] = []
    for prod_child in sorted(producers):
        for channel_id in sorted(producers[prod_child]):
            transport, topic = channel_meta.get(channel_id, ("", ""))
            for cons_child in sorted(consumers):
                if cons_child == prod_child:
                    continue
                consumer_ids = consumers[cons_child].get(channel_id)
                if not consumer_ids:
                    continue
                for from_id in producers[prod_child][channel_id]:
                    for to_id in consumer_ids:
                        edges.append(
                            CrossRepoEdge(
                                from_id=from_id,
                                to_id=to_id,
                                type="cross_repo:channel_flow",
                                props={
                                    "source_strategy": "channel_binding",
                                    "confidence": "inferred",
                                    "channel": channel_id,
                                    "transport": transport,
                                    "topic": topic,
                                },
                            )
                        )
    return edges


def _sort_key(edge: CrossRepoEdge) -> str:
    """Deterministic sort key for cross-repo edges."""
    return (
        f"{edge.from_id}\x00{edge.to_id}\x00{edge.type}\x00"
        f"{json.dumps(dict(edge.props), sort_keys=True)}"
    )


@register_resolver("channel_binding")
class ChannelBindingResolver(CrossRepoResolver):
    """Bind event-channel producers in one repo to consumers in another.

    Inspects each child's graph for ``channel`` nodes and their
    ``produces`` / ``consumes`` edges. When a producer in child A and a
    consumer in child B reference the same channel id, emits a
    ``cross_repo:channel_flow`` edge from the namespaced producing file in
    A to the namespaced consuming file in B. Registered under
    ``channel_binding`` so it is selectable via
    ``cross_repo_strategies: [channel_binding]`` in ``workspaces.yaml``.
    """

    name = "channel_binding"

    def resolve(self, context: ResolverContext) -> list[CrossRepoEdge]:
        """Produce cross-repo producer->consumer channel edges."""
        producers: dict[str, dict[str, list[str]]] = {}
        consumers: dict[str, dict[str, list[str]]] = {}
        channel_meta: dict[str, tuple[str, str]] = {}

        for child_name in sorted(context.children):
            graph = context.children[child_name]
            channels = _channel_meta(graph)
            if not channels:
                continue
            # First-writer-wins across sorted children keeps meta stable;
            # channel ids are identical across repos by construction.
            for cid, meta in channels.items():
                channel_meta.setdefault(cid, meta)
            prod = _extract_endpoints(child_name, graph, _PRODUCE_TYPE, channels)
            if prod:
                producers[child_name] = prod
            cons = _extract_endpoints(child_name, graph, _CONSUME_TYPE, channels)
            if cons:
                consumers[child_name] = cons

        edges = _build_edges(producers, consumers, channel_meta)
        return sorted(edges, key=_sort_key)
