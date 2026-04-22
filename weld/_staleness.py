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
)


def compute_stale_info(graph_path: Path, meta: dict) -> dict:
    """Return the stale-info dict for a loaded graph (ADR 0017).

    Two orthogonal signals:

    - ``source_stale`` (primary): any file in ``meta.discovered_from``
      changed content between ``meta.git_sha`` and HEAD. Agents should
      gate ``wd discover`` on this.
    - ``sha_behind`` (secondary): the recorded SHA is non-null and
      differs from HEAD.

    ``stale`` is aliased to ``source_stale`` for back-compat callers.
    Non-git roots keep the legacy ``stale=False`` + ``reason`` shape.

    Graph-only commits (bd-p1a.6) are collapsed: when the only commits
    between ``graph_sha`` and HEAD touched nothing but
    ``.weld/graph.json``, ``sha_behind`` is reported False as well. The
    graph is effectively fresh -- reporting drift in that state drives
    users into a touch/commit/touch loop because ``wd touch`` re-stamps
    HEAD, the user commits the graph, and HEAD advances again.
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
