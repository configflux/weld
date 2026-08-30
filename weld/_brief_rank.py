"""Classification and ranking helpers for the ``wd brief`` surface.

Split from ``weld.brief`` so that module stays under the 400-line lint cap
while carrying both the interaction-aware ranking (ADR 0086) and the
exact-identifier preference brief shares with ``Graph.query`` (Finding 08).

Nothing here reaches into a graph -- every function is a pure projection of a
node dict, so the brief-shape and query-parity tests can exercise it directly.
"""

from __future__ import annotations

from weld.contract import (
    BOUNDARY_KIND_VALUES,
    PROTOCOL_VALUES,
    SURFACE_KIND_VALUES,
)
from weld.ranking import exact_symbol_match_rank
from weld.ranking import rank_key as _rank_key

# Node types that count as authoritative docs/policies.
_DOC_TYPES: frozenset[str] = frozenset(["doc", "policy", "runbook"])

# Node types that count as build/verification surfaces.
_BUILD_TYPES: frozenset[str] = frozenset([
    "build-target", "test-target", "test-suite", "gate",
])

# Node types that count as boundaries/entrypoints.
_BOUNDARY_TYPES: frozenset[str] = frozenset(["boundary", "entrypoint"])

# Node types that count as interaction surfaces -- ``rpc``/``channel`` are
# the generalized Phase 7 vocabulary (ADR 0086); ROS2 interaction nodes are
# their domain-specific counterparts and belong alongside them in the
# interfaces bucket.
_INTERFACE_TYPES: frozenset[str] = frozenset([
    "rpc", "channel",
    "ros_service", "ros_action", "ros_topic", "ros_interface",
])

# Roles that signal doc-like content (ROLE_VALUES members only).
_DOC_ROLES: frozenset[str] = frozenset(["doc"])

# Roles that signal build/verification content (ROLE_VALUES members only).
_BUILD_ROLES: frozenset[str] = frozenset(["build", "test"])

# Query tokens that indicate the caller is asking about interaction surfaces.
# Hitting any of these flips interaction-aware ranking on so that interfaces
# and boundaries surface ahead of generic primary matches.
_INTERACTION_QUERY_TOKENS: frozenset[str] = frozenset([
    "interface", "interfaces", "boundary", "boundaries", "protocol",
    "protocols", "rpc", "grpc", "http", "api", "endpoint", "endpoints",
    "route", "routes", "channel", "channels", "topic", "topics", "event",
    "events", "stream", "streams", "pubsub", "pub_sub", "publish",
    "subscribe", "consumer", "producer", "handler", "handlers",
    "request", "response", "call", "calls", "invoke", "invokes",
    "ros2",
])


def has_interaction_metadata(node: dict) -> bool:
    """Return True if *node* carries any interaction-surface metadata.

    Per ADR 0086, ``protocol``, ``surface_kind``, ``transport``, and
    ``boundary_kind`` are optional props that can ride on any node type.
    A node is interaction-relevant when any of them is set to a recognized
    vocabulary value.
    """
    props = node.get("props") or {}
    protocol = props.get("protocol")
    if isinstance(protocol, str) and protocol in PROTOCOL_VALUES:
        return True
    surface_kind = props.get("surface_kind")
    if isinstance(surface_kind, str) and surface_kind in SURFACE_KIND_VALUES:
        return True
    boundary_kind = props.get("boundary_kind")
    if isinstance(boundary_kind, str) and boundary_kind in BOUNDARY_KIND_VALUES:
        return True
    # ``transport`` alone is not a reliable signal -- it is usually paired
    # with ``protocol``. Requiring at least one of the primary three props
    # avoids boosting nodes that just happen to mention a port.
    return False


def query_is_interaction_relevant(term: str) -> bool:
    """Return True if the query term mentions interaction concepts.

    Uses the same lower-cased whitespace tokenization as ``Graph.query``
    so the signal is consistent with how matches are found in the first
    place. The check is permissive: a single hit flips the flag.
    """
    tokens = term.lower().split()
    return any(tok in _INTERACTION_QUERY_TOKENS for tok in tokens)


def classify_node(node: dict) -> str:
    """Classify a node into one of:
    'doc', 'build', 'interface', 'boundary', 'primary'.

    Uses both node type and roles metadata for classification. Interfaces
    take precedence over ``primary`` but not over more specific buckets
    (docs/build/boundary) so a boundary that also declares a protocol
    stays in ``boundaries``.
    """
    ntype = node.get("type", "")
    props = node.get("props") or {}
    roles = set(props.get("roles", []))
    doc_kind = props.get("doc_kind", "")

    if (
        ntype in _DOC_TYPES
        or roles & _DOC_ROLES
        or doc_kind in ("adr", "policy", "runbook", "guide")
    ):
        return "doc"
    if (
        ntype in _BUILD_TYPES
        or roles & _BUILD_ROLES
        or doc_kind in ("gate", "verification")
    ):
        return "build"
    if ntype in _BOUNDARY_TYPES:
        return "boundary"
    if ntype in _INTERFACE_TYPES:
        return "interface"
    # Any other node that statically declares interaction-surface metadata
    # is promoted to the interfaces bucket even if its primary type is
    # something else (e.g. a ``route`` stamped with ``protocol=http``).
    if has_interaction_metadata(node):
        return "interface"
    return "primary"


def sort_key(
    node: dict,
    *,
    interaction_relevant: bool = False,
    token_groups: list[list[str]] | None = None,
) -> tuple[int, int, int, int, int, str]:
    """Sort key for brief buckets, most to least significant:

      1. ``exact_boost`` -- an exact symbol identifier hit sorts first, the
         same ``exact_symbol_match_rank`` preference ``Graph.query`` applies
         so brief agrees on which node the caller named (Finding 08). Inert
         unless *token_groups* is a single non-empty group, as in query.
      2. ``interaction_boost`` -- interaction nodes rank ahead of peers when
         the query is interaction-relevant (0 sorts first; 1 after).
      3. the shared composite ``(role, authority, confidence, id)``.

    With *interaction_relevant* False and *token_groups* None the two boosts
    are constant 0/1, keeping prior within-bucket ordering intact.
    """
    role, authority, confidence, node_id = _rank_key(node)
    exact_boost = (
        exact_symbol_match_rank(node, token_groups)
        if token_groups is not None
        else 1
    )
    if interaction_relevant and has_interaction_metadata(node):
        boost = 0
    elif interaction_relevant:
        boost = 1
    else:
        boost = 0
    return (exact_boost, boost, role, authority, confidence, node_id)


def primary_relevance(node: dict, token_groups: list[list[str]]) -> str:
    """Discriminating ``relevance`` text for a direct (non-neighbour) match.

    ``exact match`` when the node's own identifier equals the query (the same
    exact-identifier test used for ordering), else ``token match``.
    """
    return "exact match" if exact_symbol_match_rank(node, token_groups) == 0 else "token match"


def add_relevance(node: dict, reason: str) -> dict:
    """Return a copy of the node dict with a ``relevance`` field."""
    result = dict(node)
    result["relevance"] = reason
    return result
