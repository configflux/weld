"""Graph-freshness computation (ADR 0017).

Split out of :mod:`weld.graph` so the ``Graph`` class stays at its
line-count cap while the freshness rules remain directly unit-testable.
"""

from __future__ import annotations

from pathlib import Path

from weld._git import (
    commits_behind as _commits_behind,
    drift_is_graph_only,
    get_git_sha,
    is_git_repo,
    source_files_changed_since,
    working_tree_dirty_sources,
)


def compute_stale_info(graph_path: Path, meta: dict) -> dict:
    """Return the stale-info dict for a loaded graph (ADR 0017).

    Two orthogonal signals:

    - ``source_stale`` (primary): a file in ``meta.discovered_from``
      changed content -- either committed between ``meta.git_sha`` and
      HEAD, or *uncommitted in the working tree* (staged, unstaged, or a
      new untracked file under a tracked prefix). The working-tree
      dimension is what lets an agent mid-edit see its own changes; a
      commit-range-only check would report fresh until the edit landed.
      Agents should gate ``wd discover`` (and auto-refresh, ADR 0051) on
      this.
    - ``sha_behind`` (secondary): the recorded SHA is non-null and
      differs from HEAD.

    ``stale`` is aliased to ``source_stale`` for back-compat callers.
    Non-git roots keep the legacy ``stale=False`` + ``reason`` shape.

    Graph-only commits (tracked issue) are collapsed: when the only commits
    between ``graph_sha`` and HEAD touched nothing but
    ``.weld/graph.json``, ``sha_behind`` is reported False as well. The
    graph is effectively fresh -- reporting drift in that state drives
    users into a touch/commit/touch loop because ``wd touch`` re-stamps
    HEAD, the user commits the graph, and HEAD advances again. By the same
    rule, dirty ``.weld/`` bookkeeping never feeds ``source_stale`` -- the
    working-tree check excludes those paths.
    """
    root = graph_path.parent.parent  # .weld/ -> project root
    if not is_git_repo(root):
        return {
            "stale": False, "source_stale": False, "sha_behind": False,
            "graph_sha": None, "current_sha": None, "commits_behind": 0,
            "reason": "not a git repo",
        }
    cur = get_git_sha(root)
    gsha = meta.get("git_sha")
    tracked = meta.get("discovered_from") or []
    if gsha is None:
        behind = -1
    elif gsha == cur:
        behind = 0
    else:
        behind = _commits_behind(root, gsha, cur)
    sha_behind = gsha is not None and gsha != cur
    if gsha is None or behind == -1:
        source_stale = True
    elif not sha_behind:
        source_stale = False
    else:
        source_stale = bool(source_files_changed_since(root, gsha, tracked))
    # Working-tree dimension: uncommitted edits to a tracked source file
    # are drift the commit-range diff cannot see. Only consult git status
    # when the committed signal has not already flagged the graph -- this
    # keeps the already-stale paths to a single git call and runs the
    # status probe only on the clean-committed branches (where it is the
    # check that catches the agent's own in-flight edits).
    if not source_stale and working_tree_dirty_sources(root, tracked):
        source_stale = True
    # Collapse pure graph-only drift -- the graph tracks its inputs and
    # no advisory is warranted. Only applies when sources are unchanged.
    if sha_behind and not source_stale and gsha is not None:
        if drift_is_graph_only(root, gsha):
            sha_behind = False
    return {
        "stale": source_stale, "source_stale": source_stale,
        "sha_behind": sha_behind, "graph_sha": gsha,
        "current_sha": cur, "commits_behind": behind,
    }
