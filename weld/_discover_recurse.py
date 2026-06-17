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

from weld._workspace_inspect import resolve_child_root
from weld.workspace import WorkspaceConfig
from weld.workspace_state import WorkspaceState


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
    names: set[str] | None = None,
) -> RecurseResult:
    """Discover each present child in-process, return a RecurseResult.

    Only children whose ledger status is ``present`` or ``uninitialized``
    are visited; ``missing`` and ``corrupt`` children are skipped with a
    notice on stderr. Each visited child's graph is written atomically
    to its ``.weld/graph.json`` so the subsequent root rebuild sees
    fresh state.

    *names*: when given, only children whose name is in this set are
    visited (others are silently skipped -- they were intentionally
    excluded by the caller, not failed). This is the ADR 0066 part 3
    auto-recurse-on-read seam: the read-time refresh selector passes the
    *stale-or-uninitialized* subset so a one-child edit refreshes one
    child, never the whole workspace. ``None`` preserves the existing
    "visit every present/uninitialized child" behaviour used by
    ``wd discover --recurse``.

    Returns a :class:`RecurseResult` whose ``discovered`` list holds the
    names of children that were successfully refreshed, and whose
    ``errors`` dict maps name -> formatted reason for children whose
    ``_discover_single_repo`` raised.
    """
    result = RecurseResult()

    for child in sorted(config.children, key=lambda c: c.name):
        if names is not None and child.name not in names:
            # Intentionally excluded by the caller's selection -- not a
            # lifecycle skip, so no stderr notice (would be noise when the
            # selector already narrowed to the stale subset).
            continue
        entry = state.children.get(child.name)
        status = entry.status if entry else "unknown"
        if status not in ("present", "uninitialized"):
            print(
                f"[weld] recurse: skipping {child.name} (status: {status})",
                file=sys.stderr,
            )
            continue

        # ADR 0028 §1: when running from a linked worktree the child repo
        # lives only at the main checkout, not under root. Use the same
        # resolver inspect_child uses so recurse and inspection agree.
        child_root = resolve_child_root(root, child.path)
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
    """Discover a single child repo and write its graph + ADR 0065 sidecar.

    Delegates the write to ``_discover_single_repo(write_graph=True)`` -- the
    same paired writer (``write_graph_with_meta``) the standalone
    ``wd discover`` tail uses -- so the child's ``graph.json`` (volatile meta
    stripped) **and** its ``graph-meta.json`` sidecar are refreshed together,
    along with the discovery-state and derived sidecars. Writing only
    ``graph.json`` (as this path did before) left the child's sidecar holding
    the *old* discovered-from SHA; since the sidecar wins over in-graph meta
    (ADR 0065), the child-staleness oracle would keep reporting it stale on
    every subsequent read -- an auto-recurse-on-read (ADR 0066) re-refresh
    loop. The paired write also makes recurse output byte-equivalent to a
    standalone child discover.

    Returns ``None`` on success, or the captured exception instance on
    failure so the caller can record a structured error reason. The
    human-readable failure is still printed to stderr for operator
    visibility.
    """
    from weld.discover import _discover_single_repo

    print(f"[weld] recurse: discovering {name} ...", file=sys.stderr)
    (child_root / ".weld").mkdir(parents=True, exist_ok=True)
    try:
        _discover_single_repo(
            child_root, incremental=incremental, safe=safe, write_graph=True,
        )
    except Exception as exc:  # noqa: BLE001 -- per-child isolation
        print(
            f"[weld] recurse: {name} failed: {exc}",
            file=sys.stderr,
        )
        return exc

    print(f"[weld] recurse: {name} done", file=sys.stderr)
    return None
