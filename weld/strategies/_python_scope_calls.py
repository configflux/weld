"""Emit ``calls`` edges sourced at module scope (ADR 0122).

A module-level statement (``CONFIG = load_config()``) executes at import
time, in no symbol's body -- there is no function or class node to hang the
caller side on. The ``file:`` node already anchors the module (minted by
``python_module``, this strategy's declared pair per ADR 0041 Layer 3), so a
``calls`` edge sourced there states exactly what happens at import time
without minting a new "module pseudo-caller" concept.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from weld._node_ids import file_id
from weld.strategies._python_origin import (
    make_resolved_target_node,
    make_sentinel_node,
    module_from_symbol_id,
    origin_for_resolved,
    origin_for_sentinel,
)

if TYPE_CHECKING:
    from weld.strategies.python_callgraph import _CallGraphVisitor


def _file_anchor_stub(rel_path: str) -> dict:
    """Minimal ``file:`` node so a module-scope edge stays referentially closed.

    ``python_module`` normally mints the real file anchor for every file
    this strategy also parses (ADR 0041 Layer 3,
    ``weld_python_strategy_pair_test``), but only for a file that defines
    at least one class/def -- a pure-script file with a module-level call
    and no definitions would not get one. ``confidence: speculative`` means
    ADR 0103's merge rule (``claim_supersedes``) lets ``python_module``'s
    richer, ``definite`` node win regardless of which strategy's output is
    folded in first.
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


def emit_module_scope_call_edges(
    *,
    visitor: "_CallGraphVisitor",
    rel_path: str,
    project_modules: frozenset[str],
    nodes: dict[str, dict],
    edges: list[dict],
) -> None:
    """Emit one ``calls`` edge per module-level call site.

    Mirrors the symbol-sourced ``calls`` loop in ``python_callgraph.extract``
    exactly -- same node-minting rule for the target, same edge props shape
    -- except the ``from`` endpoint is the module's file anchor instead of a
    symbol id. Deduplicated per target, matching the per-caller dedup the
    symbol-sourced loop already applies.
    """
    if not visitor.module_level_calls:
        return
    from weld.strategies.python_callgraph import UNRESOLVED_PREFIX

    from_id = file_id(rel_path)
    nodes.setdefault(from_id, _file_anchor_stub(rel_path))
    seen: set[tuple[str, bool]] = set()
    for target_id, resolved, raw, line, resolution in visitor.module_level_calls:
        key = (target_id, resolved)
        if key in seen:
            continue
        seen.add(key)
        if target_id.startswith(UNRESOLVED_PREFIX):
            nodes.setdefault(
                target_id,
                make_sentinel_node(
                    target_id, resolution, origin_for_sentinel(resolution)
                ),
            )
        else:
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
                "type": "calls",
                "props": {
                    "source_strategy": "python_callgraph",
                    "confidence": "definite" if resolved else "speculative",
                    "resolved": resolved,
                    "raw": raw,
                    "resolution": resolution,
                    "provenance": {"file": rel_path, "line": line},
                },
            }
        )


__all__ = ["emit_module_scope_call_edges"]
