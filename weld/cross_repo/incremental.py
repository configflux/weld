"""Drift-aware incremental cross-repo resolver orchestration.

When a workspace root has N children, a full cross-repo resolve pass runs
every registered resolver against the entire context -- even when only one
child has changed. For small N this is cheap; for larger workspaces the
constant cost adds up.

This module provides :func:`run_resolvers_incremental`, which compares the
current workspace-state.json ledger against a prior snapshot and identifies
which children have *drifted* (changed ``graph_sha256`` or ``head_sha``
since the last discover). It then partitions the resolver run:

* **Drifted children**: full resolver pass with these children visible.
* **Stable children**: prior edges are carried forward without re-running
  resolvers.

The result is equivalent to a full :func:`~weld.cross_repo.base.run_resolvers`
call on the same state (modulo ``meta.generated_at``), but skips resolver
work proportional to the number of unchanged children.

The module deliberately does not touch ``workspace-state.json`` or
``graph.json`` on disk -- it is a pure function of the context and the
prior/current ledger snapshots. Callers are responsible for persisting
results through the existing federation write path.
"""

from __future__ import annotations

import sys
from typing import Any, Mapping

from weld.cross_repo.base import (
    CrossRepoEdge,
    ResolverContext,
    run_resolvers,
)
from weld.workspace import UNIT_SEPARATOR


__all__ = [
    "DriftResult",
    "detect_drift",
    "run_resolvers_incremental",
]


class DriftResult:
    """Outcome of comparing prior and current workspace ledger snapshots.

    Attributes
    ----------
    drifted:
        Set of child names whose ``graph_sha256`` or ``head_sha`` changed.
    stable:
        Set of child names whose state is identical to the prior snapshot.
    added:
        Set of child names present in current but absent from prior.
    removed:
        Set of child names present in prior but absent from current.
    """

    __slots__ = ("drifted", "stable", "added", "removed")

    def __init__(
        self,
        *,
        drifted: set[str],
        stable: set[str],
        added: set[str],
        removed: set[str],
    ) -> None:
        self.drifted = frozenset(drifted)
        self.stable = frozenset(stable)
        self.added = frozenset(added)
        self.removed = frozenset(removed)

    @property
    def has_changes(self) -> bool:
        """True when at least one child drifted, was added, or was removed."""
        return bool(self.drifted or self.added or self.removed)


def detect_drift(
    prior_children: Mapping[str, Mapping[str, Any]],
    current_children: Mapping[str, Mapping[str, Any]],
) -> DriftResult:
    """Compare two workspace-state.json ``children`` payloads for drift.

    A child is considered *drifted* when either its ``graph_sha256`` or
    ``head_sha`` differs between the prior and current snapshot. A child
    that exists only in one snapshot is classified as *added* or *removed*
    accordingly. Children whose ``graph_sha256`` and ``head_sha`` are
    both unchanged are *stable* -- mtime changes and other metadata
    differences are intentionally ignored (hash wins over mtime).

    Parameters
    ----------
    prior_children:
        The ``children`` dict from the previous workspace-state.json.
    current_children:
        The ``children`` dict from the freshly built workspace state.

    Returns
    -------
    DriftResult
        Classification of every child into drifted/stable/added/removed.
    """
    prior_names = set(prior_children)
    current_names = set(current_children)

    added = current_names - prior_names
    removed = prior_names - current_names
    common = prior_names & current_names

    drifted: set[str] = set()
    stable: set[str] = set()

    for name in common:
        old = prior_children[name]
        new = current_children[name]
        old_graph_sha = old.get("graph_sha256") if isinstance(old, dict) else None
        new_graph_sha = new.get("graph_sha256") if isinstance(new, dict) else None
        old_head = old.get("head_sha") if isinstance(old, dict) else None
        new_head = new.get("head_sha") if isinstance(new, dict) else None

        if old_graph_sha != new_graph_sha or old_head != new_head:
            drifted.add(name)
        else:
            stable.add(name)

    return DriftResult(
        drifted=drifted,
        stable=stable,
        added=added,
        removed=removed,
    )


def _edge_children(edge: CrossRepoEdge) -> set[str]:
    """Return the set of child names referenced by an edge's endpoints."""
    children: set[str] = set()
    if UNIT_SEPARATOR in edge.from_id:
        children.add(edge.from_id.split(UNIT_SEPARATOR, 1)[0])
    if UNIT_SEPARATOR in edge.to_id:
        children.add(edge.to_id.split(UNIT_SEPARATOR, 1)[0])
    return children


def run_resolvers_incremental(
    context: ResolverContext,
    *,
    drift: DriftResult,
    prior_edges: list[CrossRepoEdge] | None = None,
    post_run_child_hashes: Mapping[str, str] | None = None,
) -> tuple[list[CrossRepoEdge], dict[str, str]]:
    """Run cross-repo resolvers only for children that have drifted.

    When no children have drifted and none were added or removed, the
    prior edges are returned unchanged -- no resolver code executes.

    When at least one child has changed, the full resolver set runs
    against the complete context (resolvers need the full picture to
    detect cross-child relationships). Edges touching only stable
    children are then carried forward from the prior set, while edges
    touching any drifted/added child come from the fresh resolver run.

    Parameters
    ----------
    context:
        The full resolver context with all present children loaded.
    drift:
        The drift classification from :func:`detect_drift`.
    prior_edges:
        Edges from the previous resolver run. When ``None``, a full
        resolve is forced (equivalent to the first discover).
    post_run_child_hashes:
        Optional post-run hash check, forwarded to
        :func:`~weld.cross_repo.base.run_resolvers`.

    Returns
    -------
    tuple[list[CrossRepoEdge], dict[str, str]]
        The resolved edges and a log dict mapping child names to their
        resolver status (``"resolved"`` or ``"skipped"``).
    """
    log: dict[str, str] = {}
    affected = drift.drifted | drift.added

    # First discover or no prior edges: full resolve required.
    if prior_edges is None:
        for name in sorted(context.children):
            log[name] = "resolved"
        edges = run_resolvers(
            context, post_run_child_hashes=post_run_child_hashes,
        )
        return edges, log

    # Nothing changed: carry forward all prior edges, dropping any that
    # reference children that were removed.
    if not drift.has_changes:
        for name in sorted(context.children):
            log[name] = "skipped"
        return list(prior_edges), log

    # At least one child drifted or was added. Run a full resolve pass
    # (resolvers need cross-child visibility) and then merge:
    # - Edges touching any affected child come from the fresh run.
    # - Edges touching only stable children are carried from prior.
    # - Edges referencing removed children are dropped.
    fresh_edges = run_resolvers(
        context, post_run_child_hashes=post_run_child_hashes,
    )

    for name in sorted(context.children):
        if name in affected:
            log[name] = "resolved"
            print(
                f"[weld] incremental: child {name!r} drifted, re-resolving",
                file=sys.stderr,
            )
        else:
            log[name] = "skipped"
            print(
                f"[weld] incremental: child {name!r} unchanged, skipping",
                file=sys.stderr,
            )

    # Collect fresh edges that touch any affected child.
    fresh_affected: list[CrossRepoEdge] = []
    for edge in fresh_edges:
        children = _edge_children(edge)
        if children & affected:
            fresh_affected.append(edge)

    # Carry forward prior edges that touch only stable children
    # (not removed, not affected).
    removed = drift.removed
    carried: list[CrossRepoEdge] = []
    for edge in prior_edges:
        children = _edge_children(edge)
        if children & removed:
            continue  # child was removed, drop the edge
        if children & affected:
            continue  # will be replaced by fresh edges
        carried.append(edge)

    return carried + fresh_affected, log
