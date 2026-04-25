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
from dataclasses import dataclass, field
from pathlib import Path

from weld.serializer import dumps_graph as _dumps_graph
from weld.workspace import WorkspaceConfig
from weld.workspace_state import (
    WorkspaceState,
    atomic_write_text,
)


@dataclass
class RecurseResult:
    """Outcome of a recurse-children run.

    ``discovered`` lists child names whose ``_discover_single_repo`` call
    succeeded and whose ``.weld/graph.json`` was atomically refreshed.

    ``errors`` maps child name -> formatted failure reason (type +
    message) for children whose ``_discover_single_repo`` raised. Callers
    (e.g. the bootstrap orchestrator) mirror these into their own
    structured error list so the failure is visible to programmatic
    consumers -- not just on stderr.
    """

    discovered: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


def recurse_children(
    root: Path,
    config: WorkspaceConfig,
    state: WorkspaceState,
    *,
    incremental: bool | None = None,
    safe: bool = False,
) -> RecurseResult:
    """Discover each present child in-process, return a RecurseResult.

    Only children whose ledger status is ``present`` or ``uninitialized``
    are visited; ``missing`` and ``corrupt`` children are skipped with a
    notice on stderr. Each visited child's graph is written atomically
    to its ``.weld/graph.json`` so the subsequent root rebuild sees
    fresh state.

    Returns a :class:`RecurseResult` whose ``discovered`` list holds the
    names of children that were successfully refreshed, and whose
    ``errors`` dict maps name -> formatted reason for children whose
    ``_discover_single_repo`` raised.
    """
    result = RecurseResult()

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
        exc = _discover_child(
            child.name, child_root, incremental=incremental, safe=safe,
        )
        if exc is None:
            result.discovered.append(child.name)
        else:
            result.errors[child.name] = f"{type(exc).__name__}: {exc}"

    return result


def _discover_child(
    name: str,
    child_root: Path,
    *,
    incremental: bool | None = None,
    safe: bool = False,
) -> Exception | None:
    """Discover a single child repo and write its graph atomically.

    Returns ``None`` on success, or the captured exception instance on
    failure so the caller can record a structured error reason. The
    human-readable failure is still printed to stderr for operator
    visibility.
    """
    from weld.discover import _discover_single_repo

    print(f"[weld] recurse: discovering {name} ...", file=sys.stderr)
    try:
        graph = _discover_single_repo(child_root, incremental=incremental, safe=safe)
    except Exception as exc:  # noqa: BLE001 -- per-child isolation
        print(
            f"[weld] recurse: {name} failed: {exc}",
            file=sys.stderr,
        )
        return exc

    graph_path = child_root / ".weld" / "graph.json"
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(graph_path, _dumps_graph(graph))
    print(f"[weld] recurse: {name} done", file=sys.stderr)
    return None
