"""Helpers for tagging ``props.origin`` on Python-strategy nodes (ADR 0042).

Centralises the four-way origin classification used by
``python_callgraph`` so the strategy file stays focused on AST walking
and call resolution. The helpers here are pure: no I/O, no module
imports beyond ``sys`` for the stdlib list and ``builtins`` for the
builtin-name set.

See ``docs/adrs/0042-graph-node-origin.md`` §"Per-language detection
rules" (Python) for the contract.
"""

from __future__ import annotations

import builtins
import sys
from pathlib import Path
from typing import Any, Callable

#: Names that resolve to a Python built-in via implicit lookup.
_BUILTIN_NAMES: frozenset[str] = frozenset(dir(builtins))
#: Top-level module names exported by the Python standard library.
_STDLIB_MODULES: frozenset[str] = frozenset(
    getattr(sys, "stdlib_module_names", frozenset())
)


def is_stdlib_module(module: str) -> bool:
    """Return True if *module*'s first dotted segment is in the stdlib list."""
    if not module:
        return False
    root = module.split(".", 1)[0]
    return root in _STDLIB_MODULES


def is_builtin_name(name: str) -> bool:
    """Return True if *name* would resolve to a Python built-in."""
    return name in _BUILTIN_NAMES


def project_module_set(
    root: Path,
    matched: list[Path],
    excludes: list[str],
    *,
    should_skip: Callable[..., bool],
    module_dotted_path: Callable[[str], str],
) -> frozenset[str]:
    """Return the dotted module paths for project-local files in *matched*.

    Mirrors the per-file skip + ``module_dotted_path`` derivation used
    by the main extraction loop so the membership test is consistent
    with the IDs the strategy actually mints (ADR 0042 Python rule 3).
    """
    out: set[str] = set()
    for py in matched:
        if should_skip(py, excludes, root=root):
            continue
        try:
            rel_path = str(py.relative_to(root))
        except ValueError:
            continue
        dotted = module_dotted_path(rel_path)
        if dotted:
            out.add(dotted)
    return frozenset(out)


def module_from_symbol_id(symbol_id: str) -> str:
    """Extract the dotted-module portion of a ``symbol:py:<mod>:<qual>`` id."""
    parts = symbol_id.split(":", 3)
    if len(parts) >= 3 and parts[0] == "symbol" and parts[1] == "py":
        return parts[2]
    return ""


def origin_for_resolved(module: str, project_modules: frozenset[str]) -> str:
    """Origin for a resolved (non-sentinel) Python target.

    Per ADR 0042: stdlib first (``sys.stdlib_module_names`` membership
    of the first dotted segment), then project (any dotted-path match
    against this run's project file set), else external.
    """
    if not module:
        return "external"
    if is_stdlib_module(module):
        return "stdlib"
    if module in project_modules:
        return "project"
    return "external"


def origin_for_sentinel(resolution: str) -> str:
    """Origin for an unresolved-sentinel node.

    ``builtin``-tagged sentinels are stdlib (Python's implicit-lookup
    builtins live in ``builtins``); everything else is unresolved.
    """
    return "stdlib" if resolution == "builtin" else "unresolved"


def make_resolved_target_node(target_id: str, origin: str) -> dict[str, Any]:
    """Build the node payload for a resolved cross-module call target.

    Used when the call resolves to a symbol the python_callgraph strategy
    did not also walk (e.g. a stdlib or external import). The node is
    speculative (we never actually parsed its definition) but carries
    the origin tag so consumers can filter it consistently.
    """
    qualname = target_id.split(":", 2)[-1]
    module = module_from_symbol_id(target_id)
    return {
        "type": "symbol",
        "label": qualname,
        "props": {
            "module": module,
            "qualname": qualname,
            "language": "python",
            "source_strategy": "python_callgraph",
            "authority": "derived",
            "confidence": "speculative",
            "roles": ["implementation"],
            "origin": origin,
        },
    }


def make_sentinel_node(target_id: str, resolution: str, origin: str) -> dict[str, Any]:
    """Build the node payload for an unresolved-prefix sentinel."""
    qualname = target_id.split(":", 2)[-1]
    return {
        "type": "symbol",
        "label": qualname,
        "props": {
            "module": "",
            "qualname": qualname,
            "language": "python",
            "resolved": False,
            "resolution": resolution,
            "source_strategy": "python_callgraph",
            "authority": "derived",
            "confidence": "speculative",
            "roles": ["implementation"],
            "origin": origin,
        },
    }


__all__ = [
    "is_builtin_name",
    "is_stdlib_module",
    "make_resolved_target_node",
    "make_sentinel_node",
    "module_from_symbol_id",
    "origin_for_resolved",
    "origin_for_sentinel",
    "project_module_set",
]
