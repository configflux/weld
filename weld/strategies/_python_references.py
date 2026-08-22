"""Emit ``references`` edges from the python call-graph strategy.

ADR 0127 (bd lid2): a bare ``Name`` used as a VALUE -- a keyword-argument
value, a tuple/list element, an assignment RHS -- and not as a ``Call``'s
callee, is not a call: nothing is invoked. Reusing ``calls`` here would
assert something false (the same ontology violation ADR 0122 already
rejected for decorators). ``references`` is the honest, distinct
relationship: the referencing scope's own symbol (or the module's
``file:`` anchor, for a module-level statement) --references--> the
referenced symbol.

Scoped, by measurement, to same-module bare-name references that resolve
to a top-level ``def``/``class`` -- the visitor's existing "local"
resolution branch, sharing ``_CallGraphVisitor._resolve_expr_target`` with
``calls``/``decorates`` (ADR 0113/0119's "one source of truth" applied
again). Cross-module (import-table) and attribute-shaped references are
deliberately NOT recorded -- see the ADR's "Alternatives considered" --
and neither is an unresolved sentinel: a reference that does not resolve
locally carries no signal worth a graph node, and this repo alone has
thousands of bare-``Name`` loads that would otherwise flood the graph for
zero benefit.

Centralising emission here mirrors ``_python_decorates.py`` /
``_python_scope_calls.py``: the visitor collects raw-but-resolved facts,
this module turns them into edges + a lazily-minted module anchor,
keeping ``python_callgraph.py`` under the repo line-count cap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from weld._node_ids import file_id
from weld.strategies._python_origin import (
    make_resolved_target_node,
    module_from_symbol_id,
    origin_for_resolved,
)

if TYPE_CHECKING:
    from weld.strategies.python_callgraph import _CallGraphVisitor


def _file_anchor_stub(rel_path: str) -> dict:
    """Minimal ``file:`` node, mirroring ``_python_scope_calls``'s stub.

    A module-level reference needs the same lazily-materialized anchor a
    module-level call does, for the same reason: a pure-script file with
    no class/def never gets a ``python_module``-minted ``file:`` node.
    """
    return {
        "type": "file",
        "label": rel_path,
        "props": {
            "file": rel_path,
            "source_strategy": "python_callgraph",
            "authority": "derived",
            "confidence": "speculative",
            "roles": ["implementation"],
        },
    }


def _emit_one(
    *, from_id: str, rel_path: str, project_modules: frozenset[str],
    nodes: dict[str, dict], edges: list[dict], seen: set[tuple[str, str]],
    target_id: str, raw: str, line: int, resolution: str,
) -> None:
    key = (from_id, target_id)
    if key in seen:
        return
    seen.add(key)
    # Every target this population ever names is a same-module top-level
    # symbol the visitor's own ``symbols`` loop already emitted (that loop
    # always runs before this function is called) -- this mirrors the
    # calls/decorates defensive ``setdefault`` anyway, so a resolution edge
    # case can never leave a dangling endpoint.
    target_module = module_from_symbol_id(target_id)
    nodes.setdefault(
        target_id,
        make_resolved_target_node(
            target_id, origin_for_resolved(target_module, project_modules)
        ),
    )
    edges.append(
        {
            "from": from_id,
            "to": target_id,
            "type": "references",
            "props": {
                "source_strategy": "python_callgraph",
                "confidence": "definite",
                "resolved": True,
                "raw": raw,
                "resolution": resolution,
                "provenance": {"file": rel_path, "line": line},
            },
        }
    )


def emit_reference_edges(
    *,
    visitor: "_CallGraphVisitor",
    module_path: str,
    rel_path: str,
    project_modules: frozenset[str],
    nodes: dict[str, dict],
    edges: list[dict],
) -> None:
    """Emit one ``references`` edge per resolved same-module value reference.

    Deduplicated per ``(from, to)`` pair -- referencing the same symbol
    twice from the same scope (e.g. two keyword arguments both passing
    ``Tool``) is one fact, not two edges.
    """
    if not visitor.references and not visitor.module_level_references:
        return
    from weld.strategies.python_callgraph import _symbol_id

    seen: set[tuple[str, str]] = set()
    for qual, targets in visitor.references.items():
        from_id = _symbol_id(module_path, qual)
        for target_id, _resolved, raw, line, resolution in targets:
            _emit_one(
                from_id=from_id, rel_path=rel_path, project_modules=project_modules,
                nodes=nodes, edges=edges, seen=seen, target_id=target_id, raw=raw,
                line=line, resolution=resolution,
            )
    if visitor.module_level_references:
        from_id = file_id(rel_path)
        nodes.setdefault(from_id, _file_anchor_stub(rel_path))
        for target_id, _resolved, raw, line, resolution in visitor.module_level_references:
            _emit_one(
                from_id=from_id, rel_path=rel_path, project_modules=project_modules,
                nodes=nodes, edges=edges, seen=seen, target_id=target_id, raw=raw,
                line=line, resolution=resolution,
            )


__all__ = ["emit_reference_edges"]
