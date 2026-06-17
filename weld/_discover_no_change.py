"""No-content-change fast path for incremental discovery (bd 85tb.2).

When an incremental refresh finds no added/modified/deleted files and no
missing strategy outputs, the graph is already up to date. This module
builds the refreshed return without re-running any strategy: it shallow-copies
the loaded graph with a fresh ``meta`` dict (so the on-disk
``existing_graph`` stays byte-pristine for callers that compare it) and
refreshes only volatile meta. Extracted from :mod:`weld.discover` to keep
``_discover_single_repo`` under the repo line-count cap.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from weld._discover_sidecar import finalize_single_repo as _finalize_single_repo
from weld.contract import SCHEMA_VERSION
from weld._git import get_git_sha


def no_change_refresh(
    root: Path,
    existing_graph: dict,
    existing_graph_bytes: bytes | None,
    current_file_set: list[str],
    current_hashes: dict[str, str],
    *,
    with_sqlite: bool,
    write_graph: bool,
) -> dict:
    """Return the refreshed graph for the no-change path and finalize it.

    ``nodes``/``edges`` are shared by reference with *existing_graph* --
    unchanged and never mutated downstream -- so the old full ~14 MB deep
    copy is unnecessary. ``content_unchanged`` is passed to the finalizer
    only when no *non-volatile* meta changed (a ``version`` bump or a
    backfilled ``discovered_from`` would make the bytes differ from
    *existing_graph_bytes*).
    """
    print("[weld] notice: no files changed, graph is up to date", file=sys.stderr)
    refreshed = dict(existing_graph)
    refreshed["meta"] = dict(existing_graph.get("meta", {}))
    refreshed["meta"]["version"] = SCHEMA_VERSION
    refreshed["meta"]["updated_at"] = datetime.now(timezone.utc).isoformat(
        timespec="seconds",
    )
    sha = get_git_sha(root)
    if sha is not None:
        refreshed["meta"]["git_sha"] = sha
    prior_meta = existing_graph.get("meta", {})
    unchanged = (
        prior_meta.get("version") == SCHEMA_VERSION
        and bool(prior_meta.get("discovered_from"))
    )
    if not refreshed["meta"].get("discovered_from"):
        refreshed["meta"]["discovered_from"] = current_file_set
    _finalize_single_repo(
        root, current_hashes, refreshed, with_sqlite, write_graph,
        prior_graph_bytes=existing_graph_bytes,
        content_unchanged=unchanged,
    )
    return refreshed


__all__ = ["no_change_refresh"]
