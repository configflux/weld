"""Route-id and node-payload helpers for the express strategy.

Split out of :mod:`weld.strategies.express` so the regex callsite scanner
and the node/edge payload construction live apart, mirroring the
:mod:`weld.strategies._axum_routes_helpers` precedent. Keeping the payload
shape here makes it trivial to assert the route-node contract in
``weld_express_strategy_test`` without importing the whole extractor.

Layering: this module imports only stdlib and :mod:`weld._node_ids` (a
pure id helper). It re-declares the ``authority`` literal as a plain
string (``"canonical"``) rather than importing any enum from
:mod:`weld.runtime` -- the ``weld/strategies`` -> ``weld/runtime`` import
is a gate-pinned layering violation. The axum / gin / fastapi / flask /
csharp_aspnet_routes strategies stamp the same literal the same way.
"""

from __future__ import annotations

from typing import NamedTuple

from weld._node_ids import file_id as _file_id

#: ``source_strategy`` value stamped on every express-emitted node + edge.
EXPRESS_SOURCE_STRATEGY: str = "express"

#: HTTP verbs express exposes as ``app.<verb>(...)`` / ``router.<verb>(...)``
#: registration methods. ``all`` registers a handler for *every* method;
#: it is kept as the upper-cased ``ALL`` pseudo-verb (express genuinely
#: routes it) so a reader sees the catch-all registration rather than
#: dropping it. A method name outside this set (``use``, ``param``,
#: ``listen``) is ignored rather than minting a junk verb.
EXPRESS_VERBS: tuple[str, ...] = (
    "GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS", "ALL",
)


class ExpressRoute(NamedTuple):
    """One express route registration ``(method, path)`` pair.

    ``verb`` is the resolved HTTP method (upper-case), read directly from
    the ``app.<verb>`` / ``router.<verb>`` registration method name (or,
    for the ``app.route("/p").get(...)`` form, from the chained verb);
    ``path`` is the literal first-argument string of the registration
    callsite; ``source`` records the callsite grammar (``verb_call`` for
    the direct ``app.get("/p", h)`` form, ``route_chain`` for the
    ``app.route("/p").get(h)`` form) so consumers can filter populations
    the way the axum / gin / C# strategies do.
    """

    verb: str
    path: str
    source: str


def route_id(verb: str, path: str) -> str:
    """Return the canonical ``route:<VERB>:<path>`` id.

    Identical shape to the axum / gin / fastapi / flask /
    csharp_aspnet_routes convention so a polyglot graph keeps one route
    id namespace. The verb is upper-cased; the path is taken verbatim
    (express route paths are literal in source, including ``:id`` /
    ``*`` capture syntax).
    """
    return f"route:{verb.upper()}:{path}"


def route_node(*, verb: str, path: str, rel_path: str, source: str) -> dict:
    """Build an express route-node payload (ADR 0018 inbound HTTP surface).

    Mirrors the axum / gin / flask route-node prop set so cross-language
    route queries see a uniform shape. ``authority`` is the plain
    ``"canonical"`` literal (see module docstring on layering). The node
    language is ``typescript`` -- the strategy is keyed to the TypeScript
    / JS Tier-1 ladder even though the express grammar is JS-syntactic.
    """
    return {
        "type": "route",
        "label": f"{verb.upper()} {path}",
        "props": {
            "file": rel_path,
            "method": verb.upper(),
            "path": path,
            "source_strategy": EXPRESS_SOURCE_STRATEGY,
            "language": "typescript",
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

    The express registration callsite names the receiver (``app`` /
    ``router``) plus the path, but there is no statically resolvable
    *router symbol* to hang the diagnostic ``exposes`` edge on, so we
    hang it off the boundary *file* node -- the same fallback the axum /
    gin / fastapi strategies use.

    The id is built via :func:`weld._node_ids.file_id` so it matches the
    canonical ``file:`` id the TS ``tree_sitter`` strategy mints for the
    same source file (``src/app.ts`` -> ``file:src/app``); otherwise the
    ``exposes`` edge would dangle and the dangling-edge post-pass would
    drop it. ``weld._node_ids`` is a pure id helper, not ``weld.runtime``,
    so the layering invariant holds.
    """
    return _file_id(rel_path)


def boundary_file_node(rel_path: str) -> dict:
    """Build a minimal ``file:`` placeholder node for the boundary file.

    Emitted so the ``file: -> exposes -> route:`` edge survives the
    dangling-edge post-pass when the express strategy runs *without* the
    TS ``tree_sitter`` strategy paired on the same glob (e.g. a focused
    strategy test). When the pair runs, ``nodes.update`` in
    :func:`weld.discover._run` overwrites this placeholder with the
    canonical tree-sitter file node. Mirrors the axum boundary-file
    placeholder pattern.
    """
    return {
        "type": "file",
        "label": rel_path.rsplit("/", 1)[-1],
        "props": {
            "file": rel_path,
            "language": "typescript",
            "source_strategy": EXPRESS_SOURCE_STRATEGY,
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
            "source_strategy": EXPRESS_SOURCE_STRATEGY,
            "confidence": "definite",
        },
    }


__all__ = [
    "EXPRESS_SOURCE_STRATEGY",
    "EXPRESS_VERBS",
    "ExpressRoute",
    "boundary_file_id",
    "boundary_file_node",
    "exposes_edge",
    "route_id",
    "route_node",
]
