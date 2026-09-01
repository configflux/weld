"""CLI-side composition for ``wd stats`` (tracked issue).

:meth:`weld.graph.Graph.stats` returns the pure graph-level payload
(counts, description coverage, top authority nodes). The CLI additionally
surfaces:

- **Staleness**: reuses :meth:`weld.graph.Graph.stale` so operators can see
  whether the graph needs a re-discover without running ``wd stale``
  separately. This is the existing method -- we just attach it.
- **Workspace breakdown** (polyrepo only): when the current root carries
  a ``workspaces.yaml`` config, attach a compact child summary so the
  demo command shows per-repo context. Each child's lifecycle is
  **re-probed on disk at read time** (ADR 0138, extended here from
  ``wd workspace status``): the stored ``workspace-state.json`` ledger is
  a claim recorded by the last ``wd discover``, and reporting it verbatim
  meant a child deleted since then still read ``present`` on this surface
  while ``wd stale`` and ``wd workspace status`` both reported it missing.

All fields are additive -- the existing JSON schema keys returned by
``g.stats()`` are left intact for backward compatibility with pinned
consumers and test fixtures.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from weld.graph import Graph

if TYPE_CHECKING:  # annotation only -- the runtime import stays lazy below
    from weld.workspace import WorkspaceConfig


def build_stats_payload(
    root: Path,
    graph: Graph,
    *,
    top: int | None = None,
) -> dict:
    """Return the full ``wd stats`` payload for *root* and *graph*.

    ``top`` is forwarded to :meth:`weld.graph.Graph.stats` to control the
    size of the ``top_authority_nodes`` list. ``None`` keeps the historical
    cap of five for backward compatibility.

    The returned dict is a shallow copy of ``graph.stats()`` plus a
    ``stale`` field (always) and an optional ``workspaces`` block (only
    when a polyrepo workspace config is present at *root*).
    """
    payload: dict[str, Any] = dict(graph.stats(top=top))
    payload["stale"] = graph.stale()
    workspaces = _workspace_summary(root)
    if workspaces is not None:
        payload["workspaces"] = workspaces
    return payload


def _workspace_summary(root: Path) -> dict | None:
    """Return a compact workspace summary when *root* is a polyrepo root.

    Returns ``None`` when no workspace config exists.

    The per-child lifecycle comes from a read-time probe of disk, not from
    the stored ledger (ADR 0138): :func:`observed_children` re-probes every
    registered child and :func:`reconcile` overlays the result, so this
    surface counts presence exactly as ``wd stale`` and ``wd workspace
    status`` do. The stored ledger still supplies the *rest* of each
    agreeing row -- ``head_ref``, ``is_dirty`` and the recorded timestamps
    -- because reconciliation keeps an uncontradicted entry whole.

    ``drift_count`` is how many children the ledger and the disk disagree
    about. It is a count, not the rows: ``wd stats`` is a summary and
    ``wd workspace status`` is the detail surface, so duplicating the row
    shape here would mean two places to keep in step for a block this
    command does not print. It is always emitted (``0`` on agreement) so a
    consumer reads one key rather than branching on its absence.

    Falling back is failure isolation, not silence: when the probe declines
    (no readable registry, or it raised) the stored ledger is reported
    unchanged *and* the ADR 0138 notice goes to stderr, because a confident
    count sourced from a claim, with nothing to say which it was, is the
    same defect one case over.
    """
    from weld._notice import emit
    from weld._workspace_drift import (
        UNPROBED_NOTICE,
        observed_children,
        reconcile,
    )
    from weld.workspace_state import (
        WorkspaceStateError,
        load_workspace_config,
        load_workspace_state_json,
    )

    config = load_workspace_config(root)
    if config is None:
        return None

    try:
        state: dict[str, Any] = load_workspace_state_json(root)
    except WorkspaceStateError:
        # No ledger yet (``wd init`` without a ``wd discover``), or one that
        # will not load. Either way the registry is still probeable, so an
        # empty roster reconciles into the observed one.
        state = {"children": {}}
    observed = observed_children(root)
    if observed is None:
        emit(UNPROBED_NOTICE)
    state, drift = reconcile(state, observed)

    children = _child_rows(state, config)
    return {
        "count": len(children),
        "present": sum(1 for row in children if row["status"] == "present"),
        "drift_count": len(drift),
        "children": children,
    }


def _child_rows(state: dict, config: WorkspaceConfig) -> list[dict[str, Any]]:
    """Render one compact row per child, ordered by name.

    ``path`` is read from the registry, not from the ledger entry: a ledger
    row records ``graph_path`` (``<child>/.weld/graph.json``) and has no
    ``path`` key at all, so reading one emitted ``"path": null`` for every
    child whenever a ledger existed -- while the registry branch below
    filled the same key in. One key, one meaning, from the file that
    declares it.

    That branch is reached only when there is no reconciled roster to
    report: an unprobeable root that has never been discovered. ``unknown``
    is the honest status there and nowhere else -- wherever the probe ran,
    weld can see what is on disk.
    """
    declared = {child.name: child.path for child in config.children}
    entries = state.get("children")
    if isinstance(entries, dict) and entries:
        return [
            {
                "name": name,
                "status": str(entries[name].get("status", "unknown")),
                "path": declared.get(name),
                "head_ref": entries[name].get("head_ref"),
                "is_dirty": bool(entries[name].get("is_dirty")),
            }
            for name in sorted(entries)
            if isinstance(entries[name], dict)
        ]
    return [
        {"name": name, "status": "unknown", "path": declared[name]}
        for name in sorted(declared)
    ]
