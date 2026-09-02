"""Route-id and node-payload helpers for the express strategy.

Split out of :mod:`weld.strategies.express` so the regex callsite scanner
and the node/edge payload construction live apart, mirroring the
:mod:`weld.strategies._axum_routes_helpers` precedent. Keeping the payload
shape here makes it trivial to assert the route-node contract in
``weld_express_strategy_test`` without importing the whole extractor.

What is *express* about a route node lives here; what every TypeScript /
JavaScript route strategy shares -- the canonical id, the boundary-file
placeholder, the ``exposes`` edge -- lives in
:mod:`weld.strategies._ts_route_helpers` and is re-exported below under the
names this module has always published, so its callers and tests are
unaffected. The sharing is not tidiness: the placeholder carried a defect
(bd iurvv) and one copy meant one fix.

Layering: this module imports only stdlib and pure id/payload helpers. It
re-declares the ``authority`` literal as a plain string (``"canonical"``)
rather than importing any enum from :mod:`weld.runtime` -- the
``weld/strategies`` -> ``weld/runtime`` import is a gate-pinned layering
violation. The axum / gin / fastapi / flask / csharp_aspnet_routes
strategies stamp the same literal the same way.
"""

from __future__ import annotations

from typing import NamedTuple

from weld.strategies._ts_route_helpers import (
    boundary_file_id,
    boundary_file_node as _shared_boundary_file_node,
    exposes_edge as _shared_exposes_edge,
    route_id,
)

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


def route_node(*, verb: str, path: str, rel_path: str, source: str) -> dict:
    """Build an express route-node payload (ADR 0086 inbound HTTP surface).

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


def boundary_file_node(rel_path: str) -> dict:
    """Build the ``file:`` placeholder for an express route's boundary file.

    The express spelling of
    :func:`weld.strategies._ts_route_helpers.boundary_file_node`: same
    payload, this strategy's ``source_strategy``. Read that one for why the
    placeholder exists and why it states ``confidence: inferred``.
    """
    return _shared_boundary_file_node(
        rel_path, source_strategy=EXPRESS_SOURCE_STRATEGY,
    )


def exposes_edge(src: str, dst: str) -> dict:
    """Build a diagnostic express ``exposes`` edge from a boundary to a route."""
    return _shared_exposes_edge(
        src, dst, source_strategy=EXPRESS_SOURCE_STRATEGY,
    )


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
