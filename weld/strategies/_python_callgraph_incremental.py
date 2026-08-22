"""Incremental-mode helpers for the python_callgraph strategy (ADR 0074).

``python_callgraph.extract`` normally re-globs its directory pattern and
re-parses every sibling file to (a) emit symbols/edges and (b) derive the
``project_modules`` set that tags call-edge origins. On a warm incremental
refresh that is the dominant cost: a 1-file edit re-parses ~200 siblings to
keep one.

These helpers implement the dirty-scoped path. When the orchestrator passes
an :class:`weld._discover_strategies.IncrementalHint` (via the reserved
``context`` key), the strategy parses only ``matched ∩ dirty_files`` and
reconstructs ``project_modules`` from the hint's **post-purge prior node
set** -- scanning project ``python_callgraph`` symbol nodes across *all*
globs so the cross-glob union is preserved -- unioned with the dirty files'
own dotted module paths. If that reconstruction yields nothing while there
are dirty files to parse (absent/empty/incompatible prior graph), the caller
falls back to the full-glob derivation: reconstruction is an optimization;
the full path is always correct (ADR 0074 decision item 4).
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

# ``get_incremental_hint`` and ``dirty_matched`` are the strategy-agnostic hint
# primitives; they live in ``_incremental_hint`` (the neutral home now shared
# with ``python_module``, ADR 0084) and are re-exported here so
# ``python_callgraph`` keeps importing them from this module unchanged.
# ``reconstruct_project_modules`` below is the callgraph-specific piece --
# ``python_module`` has no cross-file state and needs no equivalent.
from weld._rel_path import rel_to_root
from weld.strategies._incremental_hint import (  # noqa: F401 -- re-export
    dirty_matched,
    get_incremental_hint,
)


def reconstruct_project_modules(
    prior_nodes: dict[str, dict],
    dirty_parse_files: list[Path],
    root: Path,
    *,
    module_dotted_path: Callable[[str], str],
) -> frozenset[str]:
    """Rebuild the project module set from surviving prior symbols + dirty files.

    Scans the **whole** post-purge prior node set (all globs) for ``symbol``
    nodes that this run already proved first-party
    (``origin == "project"`` and ``source_strategy == "python_callgraph"``)
    and collects their ``props.module``. Unions that with the dotted module
    paths of the dirty files being parsed this pass (which are first-party by
    definition -- they are in the project's own globs). The cross-glob scan
    is what lets a dirty file in glob A resolve a call into glob B as
    ``project`` even though glob B is not re-parsed this pass.
    """
    modules: set[str] = set()
    for node in prior_nodes.values():
        if not isinstance(node, dict) or node.get("type") != "symbol":
            continue
        props = node.get("props")
        if not isinstance(props, dict):
            continue
        if props.get("origin") != "project":
            continue
        if props.get("source_strategy") != "python_callgraph":
            continue
        module = props.get("module")
        if isinstance(module, str) and module:
            modules.add(module)
    for py in dirty_parse_files:
        try:
            rel = rel_to_root(py, root)
        except ValueError:
            continue
        dotted = module_dotted_path(rel)
        if dotted:
            modules.add(dotted)
    return frozenset(modules)


__all__ = [
    "dirty_matched",
    "get_incremental_hint",
    "reconstruct_project_modules",
]
