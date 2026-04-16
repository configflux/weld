"""Cross-repo resolver: match HTTP client calls to sibling endpoints.

The ``service_graph`` resolver is the first concrete implementation that
plugs into the cross-repo framework defined in :mod:`weld.cross_repo.base`.
It connects outbound HTTP call sites extracted by the single-repo
``http_client`` strategy to inbound endpoints declared by the
``fastapi`` and ``runtime_contract`` strategies in a sibling child repo.

Matching rules are static and intentionally narrow:

1. A client-side HTTP call is any node whose ``props.source_strategy``
   equals ``"http_client"`` and whose ``props.url`` is a full
   ``http://`` or ``https://`` URL (path-only URLs are left to the
   single-repo dangling-edge sweep and produce no cross-repo edge).
2. The URL's host segment is looked up directly against the set of
   sibling child names registered in ``workspaces.yaml``. A URL to
   ``http://services-auth:8080/tokens`` will match the sibling whose
   federated name is ``services-auth``; anything else is silently
   dropped because there is no honest static way to resolve it.
3. A server-side endpoint is any node whose ``props.source_strategy``
   is one of ``{"fastapi", "runtime_contract"}`` and whose ``label``
   parses as ``"<METHOD> <path>"``. The fastapi route id scheme
   (``route:<METHOD>:<path>``) carries the method and path in both the
   label and the id, and the runtime_contract rpc ids encode them in a
   slugged form -- both are supported via the label, keeping the
   resolver robust to either strategy's exact id spelling.
4. The client's ``(METHOD, path)`` must exactly equal the server's
   ``(METHOD, path)`` for an edge to be emitted. No normalisation,
   no trailing-slash tolerance, no prefix matching: the static-truth
   policy from ADR 0018 applies end-to-end across repos too.

Output edges are typed ``cross_repo:calls`` and carry the matched
``host``, ``port`` (``None`` when absent), ``path``, ``method``, and the
``source_strategy`` marker ``service_graph``. IDs are federated per
ADR 0011 §7: ``<child-name>\\x1f<node-id>``.

The resolver is stateless and pure; repeated calls on identical input
produce identical output (the framework's deterministic-serialization
contract extends cleanly once outputs are sorted).
"""

from __future__ import annotations

from typing import Iterable
from urllib.parse import urlsplit

from weld.cross_repo.base import (
    CrossRepoEdge,
    CrossRepoResolver,
    ResolverContext,
    register_resolver,
)
from weld.workspace import UNIT_SEPARATOR

__all__ = ["ServiceGraphResolver"]


# Surfaces whose source_strategy indicates an HTTP endpoint the resolver
# can target. Kept as a frozenset so membership checks are O(1) and the
# set is obviously immutable at the call site.
_ENDPOINT_STRATEGIES: frozenset[str] = frozenset({"fastapi", "runtime_contract"})

# HTTP methods the resolver accepts. Anything outside this set on either
# the client or the server side is treated as unparseable; we refuse to
# match rather than guess.
_HTTP_METHODS: frozenset[str] = frozenset(
    {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}
)


def _parse_client_url(url: str) -> tuple[str, int | None, str] | None:
    """Return ``(host, port, path)`` for a full URL, or ``None``.

    Path-only URLs (``/tokens``) intentionally fail this parse: they are
    already handled by the single-repo strategies' dangling-edge sweep
    and do not produce cross-repo edges even when a sibling happens to
    declare the same path.
    """
    if not url:
        return None
    split = urlsplit(url)
    if split.scheme not in ("http", "https"):
        return None
    hostname = split.hostname
    if not hostname:
        return None
    # ``urlsplit`` raises on malformed ports; catch so a bad URL in a
    # child's graph does not sink the whole resolver.
    try:
        port = split.port
    except ValueError:
        return None
    path = split.path or "/"
    return hostname, port, path


def _label_method_and_path(label: object) -> tuple[str, str] | None:
    """Decode ``"METHOD /path"`` labels emitted by fastapi + runtime_contract.

    Both strategies stamp their endpoint nodes with a label of the form
    ``"GET /users"``; the resolver uses the label rather than the node id
    so neither strategy's id scheme has to leak into this module.
    """
    if not isinstance(label, str):
        return None
    parts = label.split(" ", 1)
    if len(parts) != 2:
        return None
    method = parts[0].upper()
    path = parts[1]
    if method not in _HTTP_METHODS:
        return None
    if not path.startswith("/"):
        return None
    return method, path


def _iter_nodes(graph: object) -> Iterable[tuple[str, dict]]:
    """Iterate ``(node_id, node)`` pairs for any Graph-shaped object.

    The framework hands us :class:`weld.graph.Graph` instances in
    production and fake graphs in unit tests. Both expose ``dump()``
    returning a mapping with a ``"nodes"`` entry keyed by node id. We
    tolerate absent or malformed payloads by yielding nothing -- the
    orchestrator will drop us cleanly if a child's graph is unreadable.
    """
    dump = getattr(graph, "dump", None)
    if not callable(dump):
        return
    try:
        data = dump()
    except Exception:  # noqa: BLE001 -- never let a bad child kill the pass
        return
    if not isinstance(data, dict):
        return
    nodes = data.get("nodes")
    if not isinstance(nodes, dict):
        return
    for nid, node in nodes.items():
        if not isinstance(nid, str) or not isinstance(node, dict):
            continue
        yield nid, node


def _collect_client_calls(
    children: dict[str, object],
) -> list[tuple[str, str, str, int | None, str, str]]:
    """Return ``(child, node_id, host, port, path, method)`` per client node.

    Iteration is over the sorted child-name list so that the output
    ordering is a pure function of the child-name set, independent of
    how the caller happened to populate the mapping. ``host`` is the
    raw hostname component as extracted by :func:`urllib.parse.urlsplit`;
    callers compare it against child names case-insensitively because
    RFC 3986 declares hostnames case-insensitive while child names in
    ``workspaces.yaml`` may legitimately use mixed case.
    """
    collected: list[tuple[str, str, str, int | None, str, str]] = []
    for child_name in sorted(children):
        graph = children[child_name]
        for nid, node in _iter_nodes(graph):
            props = node.get("props")
            if not isinstance(props, dict):
                continue
            if props.get("source_strategy") != "http_client":
                continue
            method = props.get("method")
            url = props.get("url")
            if not isinstance(method, str) or not isinstance(url, str):
                continue
            method_upper = method.upper()
            if method_upper not in _HTTP_METHODS:
                continue
            parsed = _parse_client_url(url)
            if parsed is None:
                continue
            host, port, path = parsed
            collected.append(
                (child_name, nid, host, port, path, method_upper),
            )
    return collected


def _index_server_endpoints(
    children: dict[str, object],
) -> dict[str, dict[tuple[str, str], str]]:
    """Return a ``{child: {(method, path): node_id}}`` lookup table.

    The inner dict is keyed on ``(method, path)`` so that resolving a
    client call is a single dict lookup. Later endpoints overwrite
    earlier ones under the same key; in practice this only happens when
    a child's own graph contains duplicate definitions, which is a data
    issue the resolver does not try to fix.
    """
    index: dict[str, dict[tuple[str, str], str]] = {}
    for child_name, graph in children.items():
        per_child: dict[tuple[str, str], str] = {}
        for nid, node in _iter_nodes(graph):
            props = node.get("props")
            if not isinstance(props, dict):
                continue
            if props.get("source_strategy") not in _ENDPOINT_STRATEGIES:
                continue
            decoded = _label_method_and_path(node.get("label"))
            if decoded is None:
                continue
            method, path = decoded
            per_child[(method, path)] = nid
        if per_child:
            index[child_name] = per_child
    return index


def _namespaced(child_name: str, node_id: str) -> str:
    """Prefix ``node_id`` with ``<child>\\x1f`` per ADR 0011 §7."""
    return f"{child_name}{UNIT_SEPARATOR}{node_id}"


@register_resolver("service_graph")
class ServiceGraphResolver(CrossRepoResolver):
    """Resolver that wires http_client call sites to sibling endpoints.

    See module docstring for the full matching algorithm. This class is
    registered under the name ``service_graph`` so it is selectable via
    ``cross_repo_strategies: [service_graph]`` in ``workspaces.yaml``.
    """

    name = "service_graph"

    def resolve(self, context: ResolverContext) -> list[CrossRepoEdge]:
        children = dict(context.children)
        if not children:
            return []

        endpoints = _index_server_endpoints(children)
        if not endpoints:
            return []

        # Case-insensitive host -> child lookup. RFC 3986 declares
        # hostnames case-insensitive (urlsplit lower-cases them) while
        # child names in ``workspaces.yaml`` may legitimately use mixed
        # case. A direct string compare would silently miss any valid
        # uppercase child name, so we map through a lowercased index.
        child_by_host = {name.lower(): name for name in endpoints}

        calls = _collect_client_calls(children)
        edges: list[CrossRepoEdge] = []
        for client_child, client_nid, host, port, path, method in calls:
            server_child = child_by_host.get(host.lower())
            # Hosts that do not name a sibling are silently ignored: the
            # call is either external (to another org, a third-party
            # service, or the internet at large) or it targets a child
            # we cannot see this pass. Either way, there is no honest
            # edge to emit.
            if server_child is None:
                continue
            # A client whose host points at its own child is already
            # handled by the same-repo dangling-edge sweep; synthesizing
            # a cross-repo edge would duplicate that link with a
            # misleading type.
            if server_child == client_child:
                continue
            server_nid = endpoints[server_child].get((method, path))
            if server_nid is None:
                continue
            edges.append(
                CrossRepoEdge(
                    from_id=_namespaced(client_child, client_nid),
                    to_id=_namespaced(server_child, server_nid),
                    type="cross_repo:calls",
                    props={
                        "source_strategy": "service_graph",
                        "method": method,
                        "path": path,
                        "host": host,
                        "port": port,
                    },
                )
            )

        # Sort so repeated resolves on identical input produce a byte-
        # identical edge list; the framework also sorts before write,
        # but local determinism keeps unit-test assertions simple.
        edges.sort(key=lambda e: (e.from_id, e.to_id, e.type))
        return edges
