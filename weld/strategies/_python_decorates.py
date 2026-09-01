"""Emit ``decorates`` edges from the python call-graph strategy (ADR 0122).

A decorator expression executes in the ENCLOSING scope at definition time,
applied to -- not called by -- the symbol it decorates (``f = deco(f)``).
Asserting a ``calls`` edge here would be false in the general case ("lru_cache
calls f" is not true); ``decorates`` is a distinct, honest relationship:
decorator's resolved target -> decorated symbol.

Centralising emission here mirrors the established ``_python_inherits.py``
sister module: the visitor collects raw-but-resolved facts
(``visitor.decorates``), this module turns them into edges + lazily-minted
target nodes, keeping ``python_callgraph.py`` under the repo line-count cap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from weld.strategies._python_calls import ensure_call_target_node
from weld.strategies._python_import_attr import IMPORT_ATTR_PROP, import_attr_props

if TYPE_CHECKING:
    from weld.strategies.python_callgraph import _CallGraphVisitor


def emit_decorates_edges(
    *,
    visitor: "_CallGraphVisitor",
    module_path: str,
    rel_path: str,
    project_modules: frozenset[str],
    nodes: dict[str, dict],
    edges: list[dict],
) -> None:
    """Emit one ``decorates`` edge per ``visitor.decorates`` entry.

    Mirrors the ``calls``/``inherits`` edge shape: lazily materializes the
    decorator's resolved target node for referential closure, using the
    exact same node-minting rule call targets already use. The decorated
    symbol side needs no minting -- it was already emitted unconditionally
    by the caller's own symbol-node loop, since ``_record_decorators`` only
    ever fires for a qualname ``_record_symbol`` just registered.
    Deduplicated per (decorated symbol, target) pair so repeating the same
    decorator in error does not multiply edges.

    A decorator expression resolves through the same resolver a call target
    does, so it inherits the same deferral: an unresolved target the resolver
    could not decide from one glob carries ``props.import_attr`` for
    :mod:`weld._graph_closure_import_attr`. Without it the two edge kinds
    would answer the same ``@inner.deco`` differently.
    """
    if not visitor.decorates:
        return
    from weld.strategies.python_callgraph import _symbol_id

    seen: set[tuple[str, str, bool]] = set()
    for entry in visitor.decorates:
        target_id, resolved, raw, line, resolution, decorated_qual, hint = entry
        key = (decorated_qual, target_id, resolved)
        if key in seen:
            continue
        seen.add(key)
        ensure_call_target_node(nodes, target_id, resolution, project_modules)
        props: dict = {
            "source_strategy": "python_callgraph",
            "confidence": "definite" if resolved else "speculative",
            "resolved": resolved,
            "raw": raw,
            "resolution": resolution,
            "provenance": {"file": rel_path, "line": line},
        }
        if hint is not None:
            # A decorates edge runs decorator -> decorated, so the deferred
            # target is this edge's ``from`` endpoint, not its ``to``.
            props[IMPORT_ATTR_PROP] = import_attr_props(hint, "from")
        edges.append(
            {
                "from": target_id,
                "to": _symbol_id(module_path, decorated_qual),
                "type": "decorates",
                "props": props,
            }
        )


__all__ = ["emit_decorates_edges"]
