"""Shared ``wd trace`` participation contract.

The graph schema is broader than the trace surface.  These helpers keep the
walked edge set, bucketed node classes, and import diagnostics in one place so
adapters can tell whether a renderable fragment will also participate in trace.
"""

from __future__ import annotations

from collections.abc import Iterable

from weld.brief import _classify_node

TRACE_EDGE_TYPES: frozenset[str] = frozenset([
    "contains", "exposes", "consumes", "produces",
    "implements", "accepts", "responds_with",
    "verifies", "tests", "documents",
    "depends_on", "invokes", "feeds_into",
    "enforces", "orchestrates", "configures", "builds", "calls",
])

TRACE_SERVICE_TYPES: frozenset[str] = frozenset(["service", "package"])
TRACE_CONTRACT_TYPES: frozenset[str] = frozenset(["contract", "enum"])
TRACE_VERIFICATION_TYPES: frozenset[str] = frozenset([
    "test-target", "test-suite", "gate",
])
TRACE_RUNTIME_BOUNDARY_TYPES: frozenset[str] = frozenset([
    "compose", "dockerfile", "deploy",
])


def bucket_for_trace(node: dict) -> str | None:
    """Return the trace bucket name for *node*, or ``None``."""
    ntype = node.get("type", "")
    category = _classify_node(node)
    if category == "interface":
        return "interfaces"
    if category == "boundary" or ntype in TRACE_RUNTIME_BOUNDARY_TYPES:
        return "boundaries"
    if ntype in TRACE_SERVICE_TYPES:
        return "services"
    if ntype in TRACE_CONTRACT_TYPES:
        return "contracts"
    if ntype in TRACE_VERIFICATION_TYPES:
        return "verifications"
    return None


def trace_contract_warnings(fragment: dict) -> list[str]:
    """Warn when a graph fragment can render but will be inert for trace."""
    nodes = list(_nodes(fragment))
    edges = list(_edges(fragment))
    if not nodes and not edges:
        return []
    has_trace_node = any(bucket_for_trace(node) is not None for node in nodes)
    has_trace_edge = any(edge.get("type") in TRACE_EDGE_TYPES for edge in edges)
    warnings: list[str] = []
    if nodes and not has_trace_node:
        warnings.append(
            "[trace-contract] no trace bucket nodes found; wd trace buckets "
            "service/package, rpc/channel/protocol surfaces, contract/enum, "
            "boundary/entrypoint/runtime surfaces, and test/gate nodes."
        )
    if edges and not has_trace_edge:
        warnings.append(
            "[trace-contract] no trace-followed edges found; wd trace follows "
            + ", ".join(sorted(TRACE_EDGE_TYPES))
            + "."
        )
    return warnings


def _nodes(fragment: dict) -> Iterable[dict]:
    raw = fragment.get("nodes", {}) if isinstance(fragment, dict) else {}
    if not isinstance(raw, dict):
        return []
    return (
        {"id": node_id, **node}
        for node_id, node in raw.items()
        if isinstance(node, dict)
    )


def _edges(fragment: dict) -> Iterable[dict]:
    raw = fragment.get("edges", []) if isinstance(fragment, dict) else []
    if not isinstance(raw, list):
        return []
    return (edge for edge in raw if isinstance(edge, dict))
