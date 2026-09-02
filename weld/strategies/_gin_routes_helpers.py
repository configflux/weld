"""Route-id and node-payload helpers for the gin strategy (ADR 0071).

Split out of :mod:`weld.strategies.gin` so the regex callsite scanner and
the node/edge payload construction live apart, mirroring the
:mod:`weld.strategies._csharp_routes_helpers` precedent. Keeping the
payload shape here makes it trivial to assert the route-node contract in
``weld_gin_strategy_test`` without importing the whole extractor.

Layering: this module imports only stdlib. It re-declares the
``authority`` literal as a plain string (``"canonical"``) rather than
importing any enum from :mod:`weld.runtime` -- the
``weld/strategies`` -> ``weld/runtime`` import is a gate-pinned
violation (ADR 0071 § 1). The fastapi / flask / csharp_aspnet_routes
strategies stamp the same literal the same way.
"""

from __future__ import annotations

from typing import NamedTuple

from weld._node_ids import file_id as _file_id

#: ``source_strategy`` value stamped on every gin-emitted node and edge.
GIN_SOURCE_STRATEGY: str = "gin"

#: HTTP verbs gin exposes as dedicated ``RouterGroup`` methods. ``Any``
#: registers a handler for *all* verbs; it is handled separately so it
#: explodes into one route node per concrete verb (deterministic order).
GIN_VERBS: tuple[str, ...] = (
    "GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS",
)


class GinRoute(NamedTuple):
    """One gin handler-registration callsite.

    ``verb`` is the resolved HTTP method (upper-case); ``path`` is the
    literal first/relative-path argument; ``source`` records which
    callsite grammar produced it (``verb_method`` / ``any`` / ``handle``)
    so consumers can filter populations the way the C# strategy splits
    attribute vs minimal-API routes.
    """

    verb: str
    path: str
    source: str


def route_id(verb: str, path: str) -> str:
    """Return the canonical ``route:<VERB>:<path>`` id.

    Identical shape to the fastapi / flask / csharp_aspnet_routes
    convention so a polyglot graph keeps one route id namespace. The
    verb is upper-cased; the path is taken verbatim (gin paths are
    literal in source).
    """
    return f"route:{verb.upper()}:{path}"


def route_node(*, verb: str, path: str, rel_path: str, source: str) -> dict:
    """Build a gin route-node payload (ADR 0086 inbound HTTP surface).

    Mirrors the flask route-node prop set so cross-language route
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
            "source_strategy": GIN_SOURCE_STRATEGY,
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

    gin handlers are overwhelmingly closures (``func(c *gin.Context)``)
    passed inline at the registration callsite, so there is rarely a
    statically named handler symbol to expose. The ``exposes`` edge is
    diagnostic (route-node presence is the criterion-3 gate per ADR
    0071 § 1), so we hang it off the boundary *file* node -- the same
    fallback the fastapi strategy uses when no router symbol resolves.

    The id is built via :func:`weld._node_ids.file_id` so it matches the
    canonical ``file:`` id the Go ``tree_sitter`` strategy mints for the
    same source file (``main.go`` -> ``file:main``); otherwise the
    ``exposes`` edge would dangle and the dangling-edge post-pass would
    drop it. ``weld._node_ids`` is a pure id helper, not
    ``weld.runtime``, so the layering invariant (ADR 0071 § 1) holds.
    """
    return _file_id(rel_path)


def boundary_file_node(rel_path: str) -> dict:
    """Build a minimal ``file:`` placeholder node for the boundary file.

    Emitted so the ``file: -> exposes -> route:`` edge survives the
    dangling-edge post-pass when the gin strategy runs *without* the Go
    ``tree_sitter`` strategy paired on the same glob (e.g. a focused
    strategy test). Mirrors the flask handler-symbol placeholder pattern.

    When the pair *does* run, the canonical tree-sitter file node wins the
    node id, and ``confidence: inferred`` is what makes that true in both
    entry orders (bd iurvv) -- see
    :func:`weld.strategies._axum_routes_helpers.boundary_file_node` for why
    the ``nodes.update`` this docstring used to name stopped being the rule
    at ADR 0103, and what the unranked stub did instead.
    """
    return {
        "type": "file",
        "label": rel_path.rsplit("/", 1)[-1],
        "props": {
            "file": rel_path,
            "language": "go",
            "source_strategy": GIN_SOURCE_STRATEGY,
            "confidence": "inferred",
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
            "source_strategy": GIN_SOURCE_STRATEGY,
            "confidence": "definite",
        },
    }


__all__ = [
    "GIN_SOURCE_STRATEGY",
    "GIN_VERBS",
    "GinRoute",
    "boundary_file_id",
    "boundary_file_node",
    "exposes_edge",
    "route_id",
    "route_node",
]
