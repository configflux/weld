"""Cheap child-status probe for :meth:`weld.federation.FederatedGraph.children_status`.

``children_status()`` and the real read paths (``query`` / ``context`` /
``path``) have different jobs: the read paths need a queryable
:class:`~weld.graph.Graph` or :class:`~weld._sqlite_reader.SqliteBackedGraph`
handle, ``children_status()`` only needs to know whether a child is
present / missing / uninitialized / corrupt. Before bd sk3c both jobs ran
through :func:`weld.federation_child_loader.load_child`, which pays for a
handle regardless of which job asked -- classification just threw the handle
away after one ``isinstance`` check. This module is the separate, cheap path
for the classification-only job; :func:`weld.federation_child_loader.load_child`
is unchanged and still the only path a real read uses.
"""

from __future__ import annotations

import json
from pathlib import Path

from weld.federation_child_loader import (
    ReadBytes,
    graph_rel_path,
    maybe_sentinel,
)
from weld.federation_support import (
    ChildStatusResult,
    CorruptChild,
    LoadedChild,
    PresentChild,
    load_graph_bytes,
)
from weld.graph import CHILD_SCHEMA_VERSION, SchemaVersionError
from weld.workspace import ChildEntry

__all__ = ["probe_child_status"]


def probe_child_status(
    *,
    name: str,
    entry: ChildEntry,
    workspace_root: Path,
    sentinel_cache: dict[str, LoadedChild],
    read_bytes: ReadBytes,
) -> ChildStatusResult:
    """Return *name*'s ``children_status()`` classification, cheaply (bd sk3c).

    Deliberately not :func:`~weld.federation_child_loader.load_child`:
    ``children_status()`` only needs present/missing/uninitialized/corrupt,
    never a query-ready handle -- it discards whatever ``load_child`` returns
    after one ``isinstance`` check. ``load_child``'s expense is not the
    fs/git sentinel check, it is everything past it: the sqlite-fresh path
    re-hashes the full child ``graph.json`` to validate ``source_json_sha``
    (:func:`weld._sqlite_reader.sidecar_freshness`), and the JSON path
    parses the file *and* builds a full BM25 corpus, alias index and
    structural scores (:meth:`weld.graph.Graph._build_inverted_index`).
    Measured on a synthetic 20-child, ~263 KB-per-child workspace:
    ``_build_inverted_index()`` alone is ~7.5 ms of an ~8.9 ms per-child
    JSON-path cost (84%) -- and since a federated root gets a brand-new
    :class:`~weld.federation.FederatedGraph` on every MCP dispatch (no
    instance cache survives across calls,
    ``weld._mcp_read.load_graph_for_read``), that cost lands on every read.

    This stops at parse-validation: the same sentinel checks ``load_child``
    runs (:func:`~weld.federation_child_loader.maybe_sentinel`), then
    :func:`~weld.federation_support.load_graph_bytes` -- the exact
    corrupt-classification
    :func:`~weld.federation_child_loader.load_child_from_json` already
    applies before it builds an index, so "present" means precisely what it
    would after a real load: no new leniency or strictness. Calling through
    to the same ``load_graph_bytes`` (rather than re-implementing its checks
    here) is what keeps that guarantee true by construction: when
    ``load_graph_bytes`` deepened to also validate the ``nodes``/``edges``
    shape (raising ``GraphShapeError`` on a structurally incomplete but
    syntactically valid payload, e.g. ``{"meta": {...}}`` alone -- see
    :func:`weld._graph_schema.validate_graph_shape`), this probe deepened
    identically for free, with no duplicated check to keep in sync. The
    sqlite sidecar is never consulted: it is derived from ``graph.json``
    (keyed by that file's own content sha), so parsing ``graph.json`` alone
    is sufficient to classify presence, and skipping the sidecar also skips
    its full-file re-hash.

    *sentinel_cache* is read first and written on a sentinel result -- the
    same cache ``load_child`` uses -- so a probe and a real ``load_child``
    call for the same name in one request (either order) share one
    classification. Never populated on a present result: a
    :class:`~weld.federation_support.PresentChild` carries no reusable
    handle, so caching it would only shadow a later query/context call that
    needs the real one.
    """
    sentinel = sentinel_cache.get(name)
    if sentinel is not None:
        return sentinel

    child_root = workspace_root / entry.path
    graph_path = child_root / ".weld" / "graph.json"
    graph_rel = graph_rel_path(entry)

    early = maybe_sentinel(name, entry, child_root, graph_path, graph_rel)
    if early is not None:
        sentinel_cache[name] = early
        return early

    try:
        raw = read_bytes(graph_path)
    except OSError as exc:
        corrupt = CorruptChild(
            name=name, path=entry.path, graph_path=graph_rel,
            remote=entry.remote, error=f"{type(exc).__name__}: {exc}",
        )
        sentinel_cache[name] = corrupt
        return corrupt

    try:
        load_graph_bytes(
            raw, graph_path=graph_path,
            max_supported_schema_version=CHILD_SCHEMA_VERSION,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, SchemaVersionError, ValueError) as exc:
        corrupt = CorruptChild(
            name=name, path=entry.path, graph_path=graph_rel,
            remote=entry.remote, error=f"{type(exc).__name__}: {exc}",
        )
        sentinel_cache[name] = corrupt
        return corrupt

    return PresentChild(
        name=name, path=entry.path, graph_path=graph_rel, remote=entry.remote,
    )
