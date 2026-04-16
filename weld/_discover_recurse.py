"""Recursive child discovery for federated workspaces.

When ``wd discover --recurse`` is given, this module cascades discovery
into each present child repository, writes the child graph, and then
the caller rebuilds the root meta-graph.  Children are single-repo
workspaces so ``_discover_single_repo`` is called directly (no subprocess
required); the graph is written atomically to the child's ``.weld/``
directory.
"""

from __future__ import annotations

import sys
from pathlib import Path

from weld.serializer import dumps_graph as _dumps_graph
from weld.workspace import WorkspaceConfig
from weld.workspace_state import (
    WorkspaceState,
    atomic_write_text,
)


def recurse_children(
    root: Path,
    config: WorkspaceConfig,
    state: WorkspaceState,
    *,
    incremental: bool | None = None,
) -> list[str]:
    """Discover each present child in-process, return names discovered.

    Only children whose ledger status is ``present`` are visited;
    missing/uninitialized/corrupt children are skipped with a notice
    on stderr.  Each child's graph is written atomically to its
    ``.weld/graph.json`` so the subsequent root rebuild sees fresh state.

    Returns the names of children that were successfully discovered.
    """
    discovered: list[str] = []

    for child in sorted(config.children, key=lambda c: c.name):
        entry = state.children.get(child.name)
        status = entry.status if entry else "unknown"
        if status not in ("present", "uninitialized"):
            print(
                f"[weld] recurse: skipping {child.name} (status: {status})",
                file=sys.stderr,
            )
            continue

        child_root = root / child.path
        ok = _discover_child(child.name, child_root, incremental=incremental)
        if ok:
            discovered.append(child.name)

    return discovered


def _discover_child(
    name: str,
    child_root: Path,
    *,
    incremental: bool | None = None,
) -> bool:
    """Discover a single child repo and write its graph atomically."""
    from weld.discover import _discover_single_repo

    print(f"[weld] recurse: discovering {name} ...", file=sys.stderr)
    try:
        graph = _discover_single_repo(child_root, incremental=incremental)
    except Exception as exc:
        print(
            f"[weld] recurse: {name} failed: {exc}",
            file=sys.stderr,
        )
        return False

    graph_path = child_root / ".weld" / "graph.json"
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(graph_path, _dumps_graph(graph))
    print(f"[weld] recurse: {name} done", file=sys.stderr)
    return True
