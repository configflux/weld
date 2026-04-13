"""Partial-coverage and freshness warnings for interaction retrieval.

Emits explicit, stable warnings when ``cortex brief`` or ``cortex trace`` detects
that the graph is stale (not rebuilt since source changed) or that
interaction coverage is partial (e.g. a service exposes HTTP routes but
no client-side extraction, or gRPC proto nodes exist but no bindings
linked).

Warning strings follow a stable prefix convention so consuming agents
can pattern-match on severity without parsing free text:

  - ``[stale]``   -- graph freshness problem
  - ``[partial]`` -- interaction coverage gap

"""

from __future__ import annotations

from typing import Any

# -- Staleness detection -----------------------------------------------------

def check_freshness(graph: Any) -> list[str]:
    """Return warnings if the graph is stale relative to HEAD.

    Uses ``graph.stale()`` which compares the graph's ``git_sha`` against
    the current HEAD. Returns an empty list when the graph is fresh or
    when staleness cannot be determined (not a git repo).
    """
    warnings: list[str] = []
    try:
        stale_info = graph.stale()
    except Exception:
        # If stale() fails (e.g. git not available), skip silently.
        return warnings

    if not stale_info.get("stale", False):
        return warnings

    behind = stale_info.get("commits_behind", -1)
    graph_sha = stale_info.get("graph_sha")

    if graph_sha is None:
        warnings.append(
            "[stale] Graph has no recorded git_sha; "
            "run `cortex discover > .cortex/graph.json` to rebuild."
        )
    elif behind > 0:
        warnings.append(
            f"[stale] Graph is {behind} commit(s) behind HEAD; "
            f"interaction data may be outdated. "
            f"Run `cortex discover > .cortex/graph.json` to rebuild."
        )
    elif behind == -1:
        warnings.append(
            "[stale] Graph SHA not reachable from HEAD "
            "(possible force-push); interaction data may be outdated. "
            "Run `cortex discover > .cortex/graph.json` to rebuild."
        )
    else:
        # behind == 0 but stale is True -- shouldn't happen, but be safe.
        warnings.append(
            "[stale] Graph may be outdated; "
            "run `cortex discover > .cortex/graph.json` to rebuild."
        )

    return warnings

# -- Partial coverage detection -----------------------------------------------

# Protocol families that have a natural server/client pairing.
# When server-side surfaces exist but no client-side extraction is
# found (or vice versa), the coverage is partial.
_PROTOCOL_PAIRS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "http": (
        frozenset(["inbound"]),   # server side
        frozenset(["outbound"]),  # client side
    ),
    "grpc": (
        frozenset(["inbound"]),
        frozenset(["outbound"]),
    ),
}

def _collect_interaction_nodes(
    nodes: list[dict],
) -> dict[str, dict[str, set[str]]]:
    """Group interaction nodes by protocol and boundary_kind.

    Returns ``{protocol: {boundary_kind: {node_id, ...}}}``.
    Only nodes carrying both ``protocol`` and ``boundary_kind`` in
    their props are counted.
    """
    result: dict[str, dict[str, set[str]]] = {}
    for node in nodes:
        props = node.get("props") or {}
        protocol = props.get("protocol")
        bk = props.get("boundary_kind")
        if not isinstance(protocol, str) or not isinstance(bk, str):
            continue
        result.setdefault(protocol, {}).setdefault(bk, set()).add(
            node.get("id", "?")
        )
    return result

def check_partial_coverage(
    interfaces: list[dict],
    boundaries: list[dict],
    services: list[dict] | None = None,
) -> list[str]:
    """Return warnings when interaction coverage is partial.

    Checks for:
    1. Protocol pairing gaps: server-side surfaces without matching
       client-side extraction (or vice versa).
    2. Services with no interaction surfaces at all when the slice
       contains interface nodes for other services.
    """
    warnings: list[str] = []
    all_interaction = list(interfaces) + list(boundaries)
    if services is not None:
        all_interaction.extend(services)

    grouped = _collect_interaction_nodes(all_interaction)

    for protocol, (server_kinds, client_kinds) in _PROTOCOL_PAIRS.items():
        if protocol not in grouped:
            continue
        bk_map = grouped[protocol]
        has_server = any(bk_map.get(sk) for sk in server_kinds)
        has_client = any(bk_map.get(ck) for ck in client_kinds)

        if has_server and not has_client:
            warnings.append(
                f"[partial] {protocol}: server-side surfaces found "
                f"but no client-side extraction; "
                f"outbound calls may not be represented."
            )
        elif has_client and not has_server:
            warnings.append(
                f"[partial] {protocol}: client-side extraction found "
                f"but no server-side surfaces; "
                f"inbound handlers may not be represented."
            )

    # Check for proto nodes without bindings -- indicated by having
    # grpc protocol interfaces but only inbound (proto definitions)
    # with no linked implementation evidence.
    if "grpc" in grouped:
        bk_map = grouped["grpc"]
        inbound_ids = bk_map.get("inbound", set())
        internal_ids = bk_map.get("internal", set())
        if inbound_ids and not internal_ids:
            # Proto nodes exist but no bindings linked
            warnings.append(
                "[partial] grpc: proto definitions found but no "
                "gRPC bindings linked; call-site coverage may be "
                "incomplete."
            )

    return warnings

def check_confidence_gaps(nodes: list[dict]) -> list[str]:
    """Warn when a significant fraction of interaction nodes is speculative.

    Agents should know when the slice confidence is low so they can
    weight the context accordingly.
    """
    warnings: list[str] = []
    if not nodes:
        return warnings

    speculative_count = 0
    for node in nodes:
        props = node.get("props") or {}
        if props.get("confidence") == "speculative":
            speculative_count += 1

    total = len(nodes)
    if total > 0 and speculative_count / total > 0.5:
        warnings.append(
            f"[partial] {speculative_count}/{total} interaction nodes "
            f"have speculative confidence; "
            f"slice reliability may be low."
        )

    return warnings
