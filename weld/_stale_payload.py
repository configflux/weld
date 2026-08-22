"""The ``wd stale`` payload shaping funnel (ADR 0100, ADR 0096 §3).

:func:`stale_payload` is the **single** place a staleness answer is shaped,
whichever surface asks for it: ``wd stale`` (``weld._graph_cli``) and the MCP
``weld_stale`` tool (``weld._mcp_read.stale_for_root``) both call it, so the
two can never drift into answering differently. ADR 0100 exists because they
once did -- an MCP-only shaper omitted child drift from the top-level
``stale`` and dated children with a loader that cannot see the ADR 0065
sidecar. Re-deriving this shape at a call site re-opens both bugs.

The funnel is deliberately **not** federation-specific, which is why it lives
here rather than in :mod:`weld._federation_staleness`: a single repo is the
common case and gets the same treatment (its own ``compute_stale_info`` result
plus branch identity). Federation is detected inside :func:`stale_payload`,
and only then is the ADR 0066 child oracle folded in. The import direction is
one-way -- shaping imports the oracle, never the reverse -- so the oracle
stays a pure "what do I know about this child" answer with no knowledge of
the payload it ends up in.

Both functions are read-only and failure-isolated: an unreadable child
registry or a branchless checkout degrades the payload rather than raising,
because a freshness probe that crashes is worse than one that under-reports.
"""

from __future__ import annotations

from pathlib import Path

from weld._federation_staleness import aggregate_root_stale
from weld._git_worktree import get_git_branch
from weld._graph_meta_sidecar import read_sidecar_meta

__all__ = [
    "branch_identity",
    "stale_payload",
]


def branch_identity(root: Path | str) -> dict:
    """Return the additive ``{branch, graph_branch}`` pair (ADR 0096 §3).

    ``branch`` is live at *root*; ``graph_branch`` is what was checked out when
    the graph was discovered. They differ exactly when the answer comes from a
    graph built on another checkout or before a branch switch -- the silent
    wrong-branch answer this pair exists to expose.

    ``graph_branch`` is read from the sidecar, not via
    :func:`weld._graph_meta_sidecar.load_graph_meta`: ``git_branch`` is
    volatile-only by construction, so the sidecar is both the authoritative
    source and a few hundred bytes instead of a multi-MB re-parse on every
    ``wd stale``. Both fields degrade to ``None`` (detached ``HEAD``, non-git
    root, missing or unreadable sidecar) and neither ever raises.
    """
    recorded = read_sidecar_meta(Path(root) / ".weld" / "graph.json").get("git_branch")
    return {
        "branch": get_git_branch(root),
        "graph_branch": recorded if isinstance(recorded, str) else None,
    }


def stale_payload(root: Path | str, root_info: dict) -> dict:
    """Return the ``wd stale`` payload, federated-aware (ADR 0066 §2).

    *root_info* is the root graph's own
    :func:`weld._staleness.compute_stale_info` result (i.e. ``Graph.stale()``).
    At a **single repo** it is returned with only the :func:`branch_identity`
    fields added; at a **federated root** (``workspaces.yaml`` present) the
    child oracle is folded in via
    :func:`weld._federation_staleness.aggregate_root_stale`. *Every* return
    path carries branch identity, including the failure-isolated ones below --
    a root whose child registry is unreadable is precisely a root whose
    identity is worth reporting.

    The ledger is rebuilt live from the workspace config rather than read
    from a possibly-stale ``workspace-state.json``, so a child that just
    appeared or whose graph just changed is seen immediately. Building it is
    read-only (git + file-stat per child). Any failure rebuilding the ledger
    is isolated: the plain root payload is returned so ``wd stale`` never
    crashes on a federated root with an unreadable child registry.
    """
    from weld.workspace_state import build_workspace_state, load_workspace_config

    branch = branch_identity(root)
    try:
        config = load_workspace_config(root)
    except Exception:  # noqa: BLE001 -- a broken registry must not crash stale
        return {**root_info, **branch}
    if config is None:
        return {**root_info, **branch}
    try:
        state = build_workspace_state(root, config).to_dict()
    except Exception:  # noqa: BLE001 -- failure isolation (ADR 0066 part 1)
        return {**root_info, **branch}
    return {**aggregate_root_stale(root, root_info, state), **branch}
