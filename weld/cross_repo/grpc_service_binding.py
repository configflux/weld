"""Cross-repo resolver: gRPC service binding.

Matches gRPC service definitions (``rpc:grpc:*`` nodes emitted by the
``grpc_proto`` strategy) in one child repository to gRPC client stubs
and call-site edges (``invokes`` edges emitted by the ``grpc_bindings``
strategy) in other children.  For every matched pair the resolver emits
a ``cross_repo:grpc_calls`` edge whose ``from_id`` is the namespaced
stub/call-site node in the client child and ``to_id`` is the namespaced
rpc node in the service-definition child.

The matching key is the **service name** extracted from the proto
strategy's ``rpc`` nodes (``props.service``, e.g. ``catalog.v1.Catalog``)
and from the bindings strategy's ``invokes`` edges whose target id
follows the ``rpc:grpc:<service>.<method>`` convention.

Design decisions:

* Only ``present`` children are inspected.  Missing or uninitialized
  children are skipped silently -- the framework already filters them
  out of :attr:`ResolverContext.children`.
* Edge output is deterministic: edges are sorted by a canonical key
  (``from_id``, ``to_id``, ``type``, sorted-props-JSON) so that
  identical inputs produce byte-identical output across runs.
* Unmatched stubs (a client child references a service name that no
  sibling declares) produce no edges and no errors.
* When a service is renamed in the definition child, previously emitted
  edges disappear on the next discover because the matching key no
  longer aligns -- no explicit stale-edge sweep is needed.
"""

from __future__ import annotations

import json
from typing import Any

from weld._node_ids import canonical_slug
from weld.cross_repo.base import (
    CrossRepoEdge,
    CrossRepoResolver,
    ResolverContext,
    register_resolver,
)
from weld.workspace import UNIT_SEPARATOR


def _service_match_key(service_name: str) -> str:
    """Return the canonical-slug lookup key for a qualified service name.

    Per ADR 0041 § Layer 1, gRPC ``rpc`` ids route through
    ``canonical_slug`` so mixed-case service names lowercase. The
    matching key normalises both sides (``props.service`` from the
    proto strategy, which preserves the source-of-truth mixed case;
    and the lowercased segments parsed from the rpc target id) so the
    pairing is case-insensitive.
    """
    return canonical_slug(service_name)


def _extract_service_definitions(
    child_name: str,
    graph: Any,
) -> dict[str, list[str]]:
    """Return ``{canonical_service_key: [namespaced_rpc_node_id, ...]}``.

    Scans nodes of type ``rpc`` whose ``props.source_strategy`` is
    ``grpc_proto`` and whose ``props.service`` is non-empty.  The
    returned rpc node ids are already namespaced with ``child_name``.
    The dict key is the canonical-slug form of ``props.service`` so
    the lookup matches the lowercased ids minted by ``grpc_proto``.
    """
    service_to_rpcs: dict[str, list[str]] = {}
    nodes = getattr(graph, "_data", {}).get("nodes", {})
    for node_id, node in sorted(nodes.items()):
        if node.get("type") != "rpc":
            continue
        props = node.get("props", {})
        if props.get("source_strategy") != "grpc_proto":
            continue
        service = props.get("service")
        if not service:
            continue
        namespaced_id = f"{child_name}{UNIT_SEPARATOR}{node_id}"
        key = _service_match_key(service)
        service_to_rpcs.setdefault(key, []).append(namespaced_id)
    return service_to_rpcs


def _extract_grpc_call_sites(
    child_name: str,
    graph: Any,
) -> list[tuple[str, str, str, str]]:
    """Return call-site quads ``(namespaced_from, service_key, service_label, method)``.

    Scans edges with ``source_strategy == "grpc_bindings"`` and type
    ``invokes`` whose target id follows the ``rpc:grpc:<svc>.<method>``
    convention.  The ``from`` side is namespaced with ``child_name``.
    ``service_key`` is the canonical-slug lookup key (matches
    :func:`_extract_service_definitions`); ``service_label`` is the
    raw qualified service name parsed from the rpc id and is preserved
    on the emitted edge for human-readability.
    """
    call_sites: list[tuple[str, str, str, str]] = []
    edges = getattr(graph, "_data", {}).get("edges", [])
    for edge in edges:
        props = edge.get("props", {})
        if props.get("source_strategy") != "grpc_bindings":
            continue
        if edge.get("type") != "invokes":
            continue
        target = edge.get("to", "")
        if not target.startswith("rpc:grpc:"):
            continue
        # Target format: rpc:grpc:<package>.<Service>.<Method>
        qualified = target[len("rpc:grpc:"):]
        parts = qualified.rsplit(".", 1)
        if len(parts) != 2:
            continue
        service_name, method = parts
        from_id = f"{child_name}{UNIT_SEPARATOR}{edge['from']}"
        call_sites.append(
            (from_id, _service_match_key(service_name), service_name, method)
        )
    return call_sites


def _build_cross_repo_edges(
    service_defs: dict[str, dict[str, list[str]]],
    call_sites: dict[str, list[tuple[str, str, str, str]]],
) -> list[CrossRepoEdge]:
    """Match client call-sites to service definitions across children.

    ``service_defs`` maps ``child_name -> {canonical_service_key -> [rpc_ids]}``.
    ``call_sites`` maps ``child_name ->
    [(from_id, canonical_service_key, service_label, method)]``.

    For each call-site, search all *other* children for a matching
    service definition.  When found, emit one ``cross_repo:grpc_calls``
    edge per matched rpc method. The emitted edge preserves the raw
    ``service_label`` (lowercased canonical slug parsed from the
    target id) so consumers see the same shape that ``grpc_proto``
    minted.
    """
    edges: list[CrossRepoEdge] = []
    for client_child, sites in sorted(call_sites.items()):
        for from_id, service_key, service_label, method in sites:
            for def_child, defs in sorted(service_defs.items()):
                if def_child == client_child:
                    continue
                rpc_ids = defs.get(service_key)
                if rpc_ids is None:
                    continue
                # Find the specific rpc node for this method.
                method_suffix = f".{method}"
                for rpc_id in rpc_ids:
                    if rpc_id.endswith(method_suffix):
                        edges.append(
                            CrossRepoEdge(
                                from_id=from_id,
                                to_id=rpc_id,
                                type="cross_repo:grpc_calls",
                                props={
                                    "service": service_label,
                                    "method": method,
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


@register_resolver("grpc_service_binding")
class GrpcServiceBindingResolver(CrossRepoResolver):
    """Bind gRPC client stubs in one repo to service definitions in another.

    Inspects each child's graph for ``rpc`` nodes (from ``grpc_proto``)
    and ``invokes`` edges (from ``grpc_bindings``).  When a client
    call-site in child A references a service declared in child B,
    emits a ``cross_repo:grpc_calls`` edge from the namespaced call-site
    node in A to the namespaced rpc node in B.
    """

    name = "grpc_service_binding"

    def resolve(self, context: ResolverContext) -> list[CrossRepoEdge]:
        """Produce cross-repo gRPC call edges."""
        service_defs: dict[str, dict[str, list[str]]] = {}
        all_call_sites: dict[str, list[tuple[str, str, str, str]]] = {}

        for child_name in sorted(context.children):
            graph = context.children[child_name]
            defs = _extract_service_definitions(child_name, graph)
            if defs:
                service_defs[child_name] = defs
            sites = _extract_grpc_call_sites(child_name, graph)
            if sites:
                all_call_sites[child_name] = sites

        edges = _build_cross_repo_edges(service_defs, all_call_sites)
        return sorted(edges, key=_sort_key)
