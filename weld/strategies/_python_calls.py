"""Emit ``calls`` edges sourced at a defining symbol.

The symbol-sourced half of ADR 0004's call graph: one edge per call site
inside a function, method or class body, from the enclosing symbol to
whatever :meth:`_CallGraphVisitor._resolve_call` made of the target.

Carved out of :mod:`weld.strategies.python_callgraph` when that module reached
the repo line-count cap, and it is the natural seam: its module-scope sibling
(:mod:`weld.strategies._python_scope_calls`) already lived apart for the same
reason, and the two now sit next to each other rather than one inline and one
delegated. The parent keeps orchestration -- glob resolution, the project
module sets, the per-file parse -- and each edge kind's emission rules live
with the other emitters.

Unlike those siblings it is a leaf: it takes the visitor's ``calls`` mapping
rather than the visitor, and its symbol-id helpers come from
``_python_origin`` rather than being imported back out of
``python_callgraph``. That is what keeps it out of the accepted
``python_callgraph`` <-> emitter-family import cycle (ADR 0130) instead of
making it a sixth member -- the leaf already owned the id readers and the node
minters, so the two id minters belong there too, and nothing here needs the
strategy that dispatches into it.
"""

from __future__ import annotations

from weld.strategies._python_import_attr import IMPORT_ATTR_PROP, import_attr_props
from weld.strategies._python_origin import (
    UNRESOLVED_PREFIX,
    make_resolved_target_node,
    make_sentinel_node,
    module_from_symbol_id,
    origin_for_resolved,
    origin_for_sentinel,
    symbol_id,
)

#: One recorded call site: ``(target_id, resolved, raw, line, resolution,
#: import_attr_hint)`` -- what ``_CallGraphVisitor.calls`` holds per caller.
CallSite = tuple[str, bool, str, int, str, dict[str, str] | None]


def emit_symbol_call_edges(
    *,
    calls: dict[str, list[CallSite]],
    module_path: str,
    rel_path: str,
    project_modules: frozenset[str],
    nodes: dict[str, dict],
    edges: list[dict],
) -> None:
    """Emit one ``calls`` edge per call site, deduplicated within a caller.

    Materializes unresolved sentinel nodes lazily so the graph stays
    referentially closed for the orchestrator's final cleanup pass. Resolved
    cross-module targets are likewise speculatively minted so consumers always
    get an ``origin``-tagged node for every edge endpoint (ADR 0042 Python
    rules).

    An unresolved target the resolver deferred rather than gave up on carries
    ``props.import_attr``; :mod:`weld._graph_closure_import_attr` reads it back
    once the whole graph is merged. It is written onto the edge and nowhere
    else -- the sentinel *node* is a bare-name namespace shared by every call
    site that failed on the same name, so a per-site fact has no business
    there.

    The per-caller dedup is keyed on the target, which pre-dates the hint and
    still decides: two call sites in one body that fail on the same attribute
    name (``inner.work()`` and ``TABLE.work()``) were already one edge to one
    sentinel, and stay one edge carrying the first site's hint. Deterministic
    on both discover paths, since the order is the source order either way.
    """
    for caller_qual, targets in calls.items():
        from_id = symbol_id(module_path, caller_qual)
        seen: set[tuple[str, bool]] = set()
        for target_id, resolved, raw, line, resolution, hint in targets:
            if (target_id, resolved) in seen:
                continue
            seen.add((target_id, resolved))
            ensure_call_target_node(
                nodes, target_id, resolution, project_modules
            )
            edges.append(
                call_edge(
                    from_id=from_id,
                    target_id=target_id,
                    resolved=resolved,
                    raw=raw,
                    line=line,
                    resolution=resolution,
                    rel_path=rel_path,
                    hint=hint,
                )
            )


def ensure_call_target_node(
    nodes: dict[str, dict],
    target_id: str,
    resolution: str,
    project_modules: frozenset[str],
) -> None:
    """Lazily mint the node a ``calls`` edge's target end needs."""
    if target_id.startswith(UNRESOLVED_PREFIX):
        nodes.setdefault(
            target_id,
            make_sentinel_node(
                target_id, resolution, origin_for_sentinel(resolution)
            ),
        )
        return
    target_module = module_from_symbol_id(target_id)
    nodes.setdefault(
        target_id,
        make_resolved_target_node(
            target_id, origin_for_resolved(target_module, project_modules)
        ),
    )


def call_edge(
    *,
    from_id: str,
    target_id: str,
    resolved: bool,
    raw: str,
    line: int,
    resolution: str,
    rel_path: str,
    hint: dict[str, str] | None,
) -> dict:
    """Build one ``calls`` edge payload.

    Shared with the module-scope emitter, which differs only in its ``from``
    endpoint -- so the props shape is written once and the two cannot drift.
    """
    props: dict = {
        "source_strategy": "python_callgraph",
        "confidence": "definite" if resolved else "speculative",
        "resolved": resolved,
        "raw": raw,
        "resolution": resolution,
        "provenance": {"file": rel_path, "line": line},
    }
    if hint is not None:
        # A calls edge points AT its target, so that is the endpoint a
        # closure rule may move.
        props[IMPORT_ATTR_PROP] = import_attr_props(hint, "to")
    return {"from": from_id, "to": target_id, "type": "calls", "props": props}


__all__ = ["call_edge", "emit_symbol_call_edges", "ensure_call_target_node"]
