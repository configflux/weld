"""Graph-vs-HEAD staleness check for ``wd doctor``.

Factored out of :mod:`weld.doctor` to keep the entry point under the
400-line cap. The check compares ``meta.git_sha`` in
``.weld/graph.json`` against the current HEAD and reports how many
commits behind the graph is so the user knows when to re-run
``wd discover``.

Backwards compatibility: existing tests monkey-patch ``is_git_repo``,
``get_git_sha`` and ``commits_behind`` on the :mod:`weld.doctor`
module. This helper looks the symbols up via ``weld.doctor`` at call
time so those patches keep working. New patches should target this
module directly.
"""

from __future__ import annotations

import json
from pathlib import Path


def check_staleness(weld_dir: Path, root: Path, result_cls: type) -> list:
    """Return the staleness rows for ``wd doctor``.

    *result_cls* is the ``CheckResult`` shape exported by
    :mod:`weld.doctor`. Accepted as a parameter so this module has no
    circular import on the doctor entry point.
    """
    path = weld_dir / "graph.json"
    if not path.is_file():
        return []  # already covered by _check_graph_json

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    # Lookup via the doctor module so existing monkey-patches stay
    # effective. ``weld.doctor`` re-exports these names from
    # :mod:`weld._git`.
    from weld import doctor as _doctor_mod

    if not _doctor_mod.is_git_repo(root):
        return []

    current_sha = _doctor_mod.get_git_sha(root)
    # ADR 0065: git_sha now lives in the graph-meta.json sidecar (with a
    # legacy in-graph fallback). Overlay it before reading.
    from weld._graph_meta_sidecar import merge_sidecar_meta
    meta = merge_sidecar_meta(data.get("meta") or {}, path)
    graph_sha = meta.get("git_sha")

    if graph_sha is None:
        return [result_cls("warn", "graph has no git SHA -- staleness unknown", "Graph")]

    if graph_sha == current_sha:
        return []

    behind = _doctor_mod.commits_behind(root, graph_sha, current_sha) if current_sha else -1
    if behind > 0:
        suffix = "commits" if behind != 1 else "commit"
        return [
            result_cls(
                "warn",
                f"graph is {behind} {suffix} behind HEAD -- run wd discover",
                "Graph",
            )
        ]
    return [result_cls("warn", "graph is behind HEAD -- run wd discover", "Graph")]


__all__ = ["check_staleness"]
