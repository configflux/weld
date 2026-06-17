"""Route-id and node-payload helpers for the axum strategy (ADR 0071).

Split out of :mod:`weld.strategies.axum` so the regex callsite scanner and
the node/edge payload construction live apart, mirroring the
:mod:`weld.strategies._gin_routes_helpers` precedent. Keeping the payload
shape here makes it trivial to assert the route-node contract in
``weld_axum_strategy_test`` without importing the whole extractor.

Layering: this module imports only stdlib and :mod:`weld._node_ids` (a
pure id helper). It re-declares the ``authority`` literal as a plain
string (``"canonical"``) rather than importing any enum from
:mod:`weld.runtime` -- the ``weld/strategies`` -> ``weld/runtime`` import
is a gate-pinned violation (ADR 0071 § 1). The gin / fastapi / flask /
csharp_aspnet_routes strategies stamp the same literal the same way.
"""

from __future__ import annotations

from typing import NamedTuple

from weld._node_ids import file_id as _file_id

#: ``source_strategy`` value stamped on every axum-emitted node and edge.
AXUM_SOURCE_STRATEGY: str = "axum"

#: HTTP verbs axum exposes as ``axum::routing`` method-router builder
#: functions. ``.route("/p", get(h).post(h2))`` chains these, so each
#: builder named in a route's second argument mints one route node. The
#: tuple is the closed set of recognised methods; a non-matching builder
#: (axum has no ``any`` builder) is ignored rather than minting a junk
#: verb.
AXUM_VERBS: tuple[str, ...] = (
    "GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS", "TRACE",
)


class AxumRoute(NamedTuple):
    """One axum route registration ``(method, path)`` pair.

    ``verb`` is the resolved HTTP method (upper-case), derived from the
    ``axum::routing`` builder function name; ``path`` is the literal
    first-argument string of the ``.route(...)`` callsite; ``source``
    records the callsite grammar (``route_builder``) so consumers can
    filter populations the way the gin / C# strategies do.
    """

    verb: str
    path: str
    source: str


def route_id(verb: str, path: str) -> str:
    """Return the canonical ``route:<VERB>:<path>`` id.

    Identical shape to the gin / fastapi / flask / csharp_aspnet_routes
    convention so a polyglot graph keeps one route id namespace. The
    verb is upper-cased; the path is taken verbatim (axum route paths
    are literal in source).
    """
    return f"route:{verb.upper()}:{path}"


def route_node(*, verb: str, path: str, rel_path: str, source: str) -> dict:
    """Build an axum route-node payload (ADR 0018 inbound HTTP surface).

    Mirrors the gin / flask route-node prop set so cross-language route
    queries see a uniform shape. ``authority`` is the plain
    ``"canonical"`` literal (see module docstring on layering).
    """
    return {
        "type": "route",
        "label": f"{verb.upper()} {path}",
        "props": {
            "file": rel_path,
            "method": verb.upper(),
            "path": path,
            "source_strategy": AXUM_SOURCE_STRATEGY,
            "authority": "canonical",
            "confidence": "definite",
            "roles": ["implementation"],
            "protocol": "http",
            "surface_kind": "request_response",
            "transport": "http",
            "boundary_kind": "inbound",
            "declared_in": rel_path,
            "route_source": source,
        },
    }


def boundary_file_id(rel_path: str) -> str:
    """Return the canonical ``file:`` node id for *rel_path*.

    axum handlers passed to a method-router builder are usually named
    ``async fn`` items, but the registration callsite encodes only the
    builder + path, so there is no statically named *router* symbol to
    hang the diagnostic ``exposes`` edge on. Route-node presence is the
    criterion-3 gate (ADR 0071 § 1), so we hang the edge off the
    boundary *file* node -- the same fallback the gin / fastapi
    strategies use when no router symbol resolves.

    The id is built via :func:`weld._node_ids.file_id` so it matches the
    canonical ``file:`` id the Rust ``tree_sitter`` strategy mints for the
    same source file (``src/main.rs`` -> ``file:src/main``); otherwise the
    ``exposes`` edge would dangle and the dangling-edge post-pass would
    drop it. ``weld._node_ids`` is a pure id helper, not ``weld.runtime``,
    so the layering invariant (ADR 0071 § 1) holds.
    """
    return _file_id(rel_path)


def boundary_file_node(rel_path: str) -> dict:
    """Build a minimal ``file:`` placeholder node for the boundary file.

    Emitted so the ``file: -> exposes -> route:`` edge survives the
    dangling-edge post-pass when the axum strategy runs *without* the
    Rust ``tree_sitter`` strategy paired on the same glob (e.g. a focused
    strategy test). When the pair runs, ``nodes.update`` in
    :func:`weld.discover._run` overwrites this placeholder with the
    canonical tree-sitter file node. Mirrors the gin boundary-file
    placeholder pattern.
    """
    return {
        "type": "file",
        "label": rel_path.rsplit("/", 1)[-1],
        "props": {
            "file": rel_path,
            "language": "rust",
            "source_strategy": AXUM_SOURCE_STRATEGY,
            "roles": ["implementation"],
        },
    }


def exposes_edge(src: str, dst: str) -> dict:
    """Build a diagnostic ``exposes`` edge from a boundary to a route."""
    return {
        "from": src,
        "to": dst,
        "type": "exposes",
        "props": {
            "source_strategy": AXUM_SOURCE_STRATEGY,
            "confidence": "definite",
        },
    }


__all__ = [
    "AXUM_SOURCE_STRATEGY",
    "AXUM_VERBS",
    "AxumRoute",
    "boundary_file_id",
    "boundary_file_node",
    "exposes_edge",
    "route_id",
    "route_node",
]
