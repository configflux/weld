"""Federated file-index fan-out for ``wd find`` / ``weld_find`` (ADR 0089).

``wd find`` reads the keyword file-index, not the graph. At a federated root the
root's own index covers only root-level files -- child files live behind nested
git boundaries that ``iter_repo_files`` stops at, so a plain root ``find`` never
reaches them. :func:`federated_find` fans the search out across the workspace
root plus every child's index and merges the results.

Child file paths are prefixed with the child name (``<child>/<path>``) so results
are disambiguated and stay workspace-relative (children are nested dirs under the
root). The merged list is re-ranked by ``(score desc, path asc)`` -- a total
order over deterministic inputs (children visited in sorted name order), so the
answer is byte-stable (ADR 0012) and identical on the CLI and MCP surfaces (the
ADR 0083 thin-wrapper invariant).
"""

from __future__ import annotations

from pathlib import Path

from weld.file_index import load_file_index
from weld.file_index_search import find_files
from weld.workspace_state import load_workspace_config

__all__ = ["federated_find"]


def federated_find(root: Path | str, term: str, limit: int | None = None) -> dict:
    """Return a ``find`` envelope spanning the workspace root + every child.

    Mirrors the single-repo :func:`weld.file_index_search.find_files` envelope
    shape (``{"query", "files": [{"path", "score", "tokens"}, ...]}``). Each
    source is searched unbounded, then the union is ranked and truncated to
    *limit* (``limit is not None`` slices to ``max(limit, 0)``, matching
    ``find_files``).
    """
    root = Path(root)
    files: list[dict] = list(
        find_files(load_file_index(root), term).get("files", [])
    )
    config = load_workspace_config(root)
    if config is not None:
        for child in sorted(config.children, key=lambda entry: entry.name):
            child_index = load_file_index(root / child.path)
            for entry in find_files(child_index, term).get("files", []):
                prefixed = dict(entry)
                prefixed["path"] = f"{child.name}/{entry.get('path', '')}"
                files.append(prefixed)
    files.sort(key=lambda e: (-int(e.get("score", 0) or 0), str(e.get("path", ""))))
    if limit is not None:
        files = files[: max(limit, 0)]
    return {"query": term, "files": files}
