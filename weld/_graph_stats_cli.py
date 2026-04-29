"""CLI-side composition for ``wd stats`` (tracked issue).

:meth:`weld.graph.Graph.stats` returns the pure graph-level payload
(counts, description coverage, top authority nodes). The CLI additionally
surfaces:

- **Staleness**: reuses :meth:`weld.graph.Graph.stale` so operators can see
  whether the graph needs a re-discover without running ``wd stale``
  separately. This is the existing method -- we just attach it.
- **Workspace breakdown** (polyrepo only): when the current root carries
  a ``workspaces.yaml`` config, attach a compact child summary so the
  demo command shows per-repo context. The breakdown uses the
  workspace-state ledger when present (:func:`load_workspace_state_json`)
  and falls back to the declared child list when no ledger snapshot has
  been written yet (first-run before ``wd discover``).

All fields are additive -- the existing JSON schema keys returned by
``g.stats()`` are left intact for backward compatibility with pinned
consumers and test fixtures.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from weld.graph import Graph


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

    Prefers the ledger at ``.weld/workspace-state.json`` because that
    snapshot already records per-child git status; falls back to the
    declared ``workspaces.yaml`` child list when no ledger has been
    written yet (``wd init`` without a subsequent ``wd discover``).
    Returns ``None`` when no workspace config exists.
    """
    from weld.workspace_state import (
        WorkspaceStateError,
        load_workspace_config,
        load_workspace_state_json,
    )

    config = load_workspace_config(root)
    if config is None:
        return None

    children: list[dict[str, Any]]
    try:
        state = load_workspace_state_json(root)
    except WorkspaceStateError:
        state = None
    if state is not None and isinstance(state.get("children"), dict):
        children = []
        for name in sorted(state["children"]):
            entry = state["children"][name]
            if not isinstance(entry, dict):
                continue
            children.append({
                "name": name,
                "status": str(entry.get("status", "unknown")),
                "path": entry.get("path"),
                "head_ref": entry.get("head_ref"),
                "is_dirty": bool(entry.get("is_dirty")),
            })
    else:
        children = [
            {"name": child.name, "status": "unknown", "path": child.path}
            for child in sorted(
                config.children, key=lambda entry: entry.name,
            )
        ]

    return {"count": len(children), "children": children}
