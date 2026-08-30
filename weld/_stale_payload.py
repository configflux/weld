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

All three functions are read-only and failure-isolated: an unreadable child
registry, a branchless checkout, or a root the seeding probe cannot reach
degrades the payload rather than raising, because a freshness probe that
crashes is worse than one that under-reports.
"""

from __future__ import annotations

from pathlib import Path

from weld._federation_staleness import aggregate_root_stale
from weld._git_worktree import get_git_branch
from weld._graph_meta_sidecar import read_sidecar_meta
from weld._staleness import NO_GRAPH_REASON

__all__ = [
    "branch_identity",
    "seed_block_detail",
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


def seed_block_detail(root: Path | str, root_info: dict) -> dict:
    """Return the optional ``{seed_blocked_reason}`` pair, or ``{}``.

    ADR 0100 amendment (bd kgx83). ``reason: no graph`` is a true answer and
    keeps its exit 0 (ADR 0134 leaves it alone), but on its own it points the
    reader at ``wd discover`` -- which is the wrong remedy in the one case
    where no ``wd`` command can help: a linked worktree of a repository that
    never carries ``.weld/discover.yaml``, where seeding can never fire and
    the fix is a repository-wide ``git add -f``. That is the surface CLAUDE.md
    tells an agent to check *first* in a new worktree, and an agent has no
    terminal to consult for a second opinion.

    Two conditions, and the first is the gate:

    * ``reason`` is :data:`weld._staleness.NO_GRAPH_REASON`. A checkout that
      *has* a graph has no seeding question left to answer, whatever its
      config situation, so the key stays absent there even when a cause is
      perfectly computable.
    * :func:`weld._worktree_seed.seed_blocked_reason` names one. It declines
      for every graphless state the standing answer already serves -- a plain
      clone, the main checkout, a worktree that has its config, a federated
      root -- so those keep today's payload byte for byte.

    The cause is the CLI's own function rather than a restatement, exactly as
    :func:`weld._mcp_guard.missing_graph_payload` reuses it: a second copy of
    the rule is how two surfaces come to disagree about it. Imported per call
    to keep this module's import graph flat, and because the probe only ever
    runs on the no-graph branch -- a served freshness answer never reaches it.

    Read-only by construction: two ``stat`` calls and the pair of
    ``git rev-parse`` probes :func:`weld._git_worktree.is_linked_worktree`
    runs, never :func:`weld._worktree_seed.ensure_seeded`. ``wd stale`` under
    ``--no-refresh`` must stay a pure read, and this runs after that flag has
    already declined the seed.
    """
    if root_info.get("reason") != NO_GRAPH_REASON:
        return {}
    from weld._worktree_seed import seed_blocked_reason

    cause = seed_blocked_reason(root)
    return {"seed_blocked_reason": cause} if cause else {}


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

    :func:`seed_block_detail` rides the same four paths for the same reason,
    and is why the ADR 0100 amendment needed no MCP code at all: this is the
    single shaper both ``wd stale`` and ``weld_stale`` pass through, so a key
    added here reaches both surfaces or neither. It contributes ``{}`` in
    every state but one, so no existing payload changes.

    The ledger is rebuilt live from the workspace config rather than read
    from a possibly-stale ``workspace-state.json``, so a child that just
    appeared or whose graph just changed is seen immediately. Building it is
    read-only (git + file-stat per child). Any failure rebuilding the ledger
    is isolated: the plain root payload is returned so ``wd stale`` never
    crashes on a federated root with an unreadable child registry.
    """
    from weld.workspace_state import build_workspace_state, load_workspace_config

    added = {**branch_identity(root), **seed_block_detail(root, root_info)}
    try:
        config = load_workspace_config(root)
    except Exception:  # noqa: BLE001 -- a broken registry must not crash stale
        return {**root_info, **added}
    if config is None:
        return {**root_info, **added}
    try:
        state = build_workspace_state(root, config).to_dict()
    except Exception:  # noqa: BLE001 -- failure isolation (ADR 0066 part 1)
        return {**root_info, **added}
    return {**aggregate_root_stale(root, root_info, state), **added}
