"""Best-effort sidecar persistence for the discovery orchestrator.

Hosts the helpers ``_discover_single_repo`` calls after each graph build
to keep ``wd query`` (query-state sidecar), federation reads (sqlite
sidecar), and ``wd find`` (file index) in sync with the freshly-written
``graph.json``. Carved out of :mod:`weld.discover` to keep that module
under the 400-line cap (CLAUDE.md "Line-Count Policy").

Failures inside every helper are logged and swallowed -- a missing
sidecar simply means the next cold load rebuilds and writes one itself;
we never let an indexing hiccup fail the whole discovery run.

Performance (bd 85tb.2): the query-state and sqlite writers accept the
already-serialized canonical ``graph_bytes`` so the caller serializes
once and the two sidecars stop re-running ``dumps_graph`` (~900 ms each
on a 6.5k-node graph). They also skip their rebuild outright when the
on-disk sidecar already pins those exact bytes. The file index refreshes
incrementally via :mod:`weld._file_index_incremental` -- re-tokenizing
only changed files instead of re-parsing every Python AST on every refresh.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from weld._query_sidecar import write_sidecar_for_bytes as _write_query_sidecar_bytes
from weld.serializer import dumps_graph as _dumps_graph
from weld.serializer import dumps_graph_canonical as _dumps_graph_canonical


def _query_sidecar_already_fresh(
    weld_dir: Path, graph_bytes: bytes, node_count: int, edge_count: int,
) -> bool:
    """True if ``query_state.bin`` already pins exactly *graph_bytes*.

    bd 85tb.2: a no-change / small-change refresh that lands a
    byte-identical ``graph.json`` leaves the existing sidecar valid, so
    rebuilding the inverted index + BM25 corpus is wasted work. We require
    every field the reader's :func:`weld._query_sidecar._envelope_matches`
    gate checks (magic, format/schema version, graph digest, node/edge
    counts) so a skip can never leave a sidecar the reader would reject --
    which would silently put us back on the rebuild-on-read path -- nor one
    it would wrongly accept. Any read or parse error returns False
    (rebuild), never raises.
    """
    try:
        import pickle

        from weld._query_sidecar import (
            SIDECAR_FILENAME, _MAGIC, _FORMAT_VERSION, _weld_schema_version,
        )

        path = weld_dir / SIDECAR_FILENAME
        if not path.is_file():
            return False
        envelope = pickle.loads(path.read_bytes())
        if not isinstance(envelope, dict):
            return False
        digest = hashlib.sha256(graph_bytes).hexdigest()
        return (
            envelope.get("magic") == _MAGIC
            and envelope.get("format_version") == _FORMAT_VERSION
            and envelope.get("weld_schema_version") == _weld_schema_version()
            and envelope.get("graph_sha256") == digest
            and envelope.get("node_count") == node_count
            and envelope.get("edge_count") == edge_count
        )
    except Exception:  # noqa: BLE001 -- a bad cache is just a rebuild.
        return False


def persist_query_state_sidecar(
    weld_dir: Path, graph: dict, *, graph_bytes: bytes | None = None,
) -> None:
    """Write the .weld/query_state.bin sidecar for the freshly-built graph.

    ADR 0031: the inverted index, BM25 corpus, and structural-score
    table are pure functions of the graph's node and edge sets and
    dominate the ``wd query`` cold path. Persisting them here makes
    the next cold ``Graph.load`` skip the rebuild.

    *graph_bytes* lets the caller pass the already-serialized canonical
    JSON so this helper does not re-run ``dumps_graph``. The bytes MUST
    be the exact canonical serialization of *graph* -- the sidecar's
    freshness digest is taken over them and must match the bytes that
    land in ``graph.json``. When omitted, the helper serializes itself.

    bd 85tb.2: when the on-disk sidecar already pins these exact bytes
    (a byte-identical refresh), the rebuild is skipped entirely.
    """
    try:
        from weld.query_state import build_query_state

        nodes = graph.get("nodes", {})
        edges = graph.get("edges", [])
        if graph_bytes is None:
            graph_bytes = _dumps_graph(graph).encode("utf-8")
        if _query_sidecar_already_fresh(
            weld_dir, graph_bytes, len(nodes), len(edges),
        ):
            return
        state = build_query_state(nodes, edges)
        _write_query_sidecar_bytes(weld_dir, graph_bytes, nodes, edges, state)
    except Exception as exc:  # noqa: BLE001 -- sidecar is best-effort.
        print(
            f"[weld] notice: skipped query-state sidecar write: {exc}",
            file=sys.stderr,
        )


def _sqlite_sidecar_already_fresh(target: Path, graph_bytes: bytes) -> bool:
    """True if ``graph.db`` already records exactly *graph_bytes*.

    bd 85tb.2: skip the (multi-thousand-row) sqlite rebuild when a
    byte-identical refresh leaves the existing sidecar valid. We mirror the
    reader's :func:`weld._sqlite_reader.sidecar_freshness` gate (ADR 0058):
    both the recorded ``source_json_sha`` and ``sqlite_schema_version`` must
    match, so a skip can never leave a sidecar the reader would reject
    (which would silently force a rebuild-on-read) nor one it would wrongly
    accept. Any sqlite/IO error returns False (rebuild), never raises.
    """
    try:
        import sqlite3

        from weld._sqlite_reader import read_meta
        from weld._sqlite_schema import (
            META_KEY_SOURCE_JSON_SHA,
            META_KEY_SQLITE_SCHEMA_VERSION,
            SQLITE_SCHEMA_VERSION,
        )
        from weld._sqlite_writer import compute_source_json_sha

        if not target.is_file():
            return False
        conn = sqlite3.connect(str(target))
        try:
            meta = read_meta(conn)
        finally:
            conn.close()
        return (
            meta.get(META_KEY_SOURCE_JSON_SHA) == compute_source_json_sha(graph_bytes)
            and str(meta.get(META_KEY_SQLITE_SCHEMA_VERSION))
            == str(SQLITE_SCHEMA_VERSION)
        )
    except Exception:  # noqa: BLE001 -- a bad/locked cache is just a rebuild.
        return False


def persist_sqlite_sidecar(
    weld_dir: Path, graph: dict, *, graph_bytes: bytes | None = None,
) -> None:
    """Write the .weld/graph.db sqlite sidecar for the freshly-built graph (ADR 0058).

    The canonical JSON is the single source of truth (ADR 0011); this
    sidecar is a derived index that lets federation reads avoid
    loading every child's JSON. Hashed against the same canonical
    bytes the writer emits to ``graph.json``, so the reader's
    freshness check is exact.

    *weld_dir* is the directory that holds (or will hold) ``graph.json``
    and ``graph.db``. The reader's freshness contract pairs them by
    that exact filename; callers using ``wd discover --output
    custom.json`` are expected to ensure *weld_dir* is the same
    directory as the JSON output (so the SHA basis matches).

    *graph_bytes* is the already-serialized canonical JSON; when passed,
    the sqlite build reuses it instead of re-running ``dumps_graph``
    (bd 85tb.2). It MUST equal the canonical serialization of *graph*.
    When the on-disk sidecar already records these exact bytes, the
    rebuild is skipped entirely.
    """
    try:
        from weld._sqlite_writer import safe_build_sidecar_for_bytes, sidecar_path_for

        target = sidecar_path_for(weld_dir / "graph.json")
        if graph_bytes is None:
            graph_bytes = _dumps_graph(graph).encode("utf-8")
        if _sqlite_sidecar_already_fresh(target, graph_bytes):
            return
        safe_build_sidecar_for_bytes(graph, graph_bytes, target)
    except Exception as exc:  # noqa: BLE001 -- sidecar is best-effort.
        print(
            f"[weld] notice: skipped sqlite sidecar write: {exc}",
            file=sys.stderr,
        )


def persist_file_index(root: Path) -> None:
    """Refresh the keyword-to-file index alongside the graph.

    The file index backs ``wd find`` and is functionally a sibling of
    ``graph.json``: callers expect both to be in sync after a
    discovery run. Historically only the standalone ``wd build-index``
    verb wrote it, so a fresh checkout that ran ``wd discover`` could
    leave ``wd find`` returning empty results for symbols that clearly
    existed on disk and in the graph (the canonical dogfood gap).

    bd 85tb.2: try the incremental refresh first (re-tokenize only the
    files whose content changed since the last write). When no usable
    companion exists -- first run, wiped index, schema bump -- fall back
    to a full rebuild that also seeds the companion so the *next* refresh
    can go incremental. Both paths produce a byte-identical
    ``file-index.json`` (ADR 0012 §3): ``save_file_index`` re-sorts the
    final map, so output bytes depend only on its content.
    """
    try:
        from weld._file_index_incremental import refresh_file_index, reindex_full

        if refresh_file_index(root) is None:
            reindex_full(root)
    except Exception as exc:  # noqa: BLE001 -- index refresh is best-effort.
        print(
            f"[weld] notice: skipped file-index refresh: {exc}",
            file=sys.stderr,
        )


def _canonical_on_disk_bytes(
    graph: dict, prior_graph_bytes: bytes | None,
) -> tuple[bytes, bool]:
    """Return ``(stripped_bytes, reused_prior)`` for *graph* (ADR 0065).

    ``graph.json`` is written with the two volatile meta keys stripped.
    When *prior_graph_bytes* (the bytes already read from the on-disk
    ``graph.json`` this run) describe a graph whose *non-volatile* meta is
    identical to *graph*'s, the stripped serialization is provably
    byte-identical to those prior bytes -- so we reuse them, skip a ~900 ms
    re-serialization of the 14 MB graph, and flag ``reused_prior=True`` so
    the caller can also skip rewriting an identical ``graph.json`` body
    (bd 85tb.2, the no-change fast path). Otherwise we serialize the
    stripped graph and flag ``reused_prior=False``.
    """
    from weld._graph_meta_sidecar import split_volatile_meta

    on_disk, _volatile = split_volatile_meta(graph)
    if prior_graph_bytes is not None:
        try:
            prior = json.loads(prior_graph_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            prior = None
        if isinstance(prior, dict):
            prior_on_disk, _ = split_volatile_meta(prior)
            if prior_on_disk == on_disk:
                return prior_graph_bytes, True
    # *graph* comes from post_process (or is a graph loaded back from a
    # canonical graph.json), so it is already in canonical shape -- skip the
    # redundant re-canonicalization (bd 85tb.2). dumps_graph_canonical falls
    # back to the full path automatically if that ever stops holding.
    return _dumps_graph_canonical(on_disk).encode("utf-8"), False


def finalize_single_repo(
    root: Path,
    current_hashes: dict[str, str],
    graph: dict,
    with_sqlite: bool,
    write_graph: bool,
    *,
    prior_graph_bytes: bytes | None = None,
    content_unchanged: bool = False,
) -> None:
    """Persist discovery-state, every derived sidecar, and (optionally)
    ``graph.json`` -- the single tail every ``_discover_single_repo`` exit
    path runs.

    The canonical graph JSON is serialized exactly once (here) and threaded
    into the query-state and sqlite sidecar writers so neither re-runs
    ``dumps_graph`` (bd 85tb.2: ~900 ms apiece on this repo); the file index
    refreshes incrementally. The bytes are the *volatile-stripped* bytes
    (ADR 0065) -- exactly what ``write_graph_with_meta`` writes to
    ``graph.json`` -- so (a) a cold ``Graph.load`` HITS the sidecars instead
    of always missing on the ``updated_at`` delta, and (b) a no-change /
    small-change refresh leaving a byte-identical ``graph.json`` lets the
    writers skip their rebuilds (the on-disk sidecar already matches).

    *content_unchanged* (bd 85tb.2): the no-change fast path passes this with
    *prior_graph_bytes* equal to the current on-disk ``graph.json`` body and
    a *graph* whose node/edge content is byte-identical to it. We then take
    those bytes verbatim -- no serialize and no comparison parse -- and
    skip the graph.json body rewrite entirely.

    When *write_graph* is set, ``graph.json`` and its ADR 0065 sidecar are
    written here from those same bytes -- no second serialization. The
    standalone ``wd discover`` CLI leaves it ``False`` and owns its own
    ``--output`` write, preserving the pure build-and-return shape.
    """
    from weld._discover_state_check import save_state_for_graph
    from weld._graph_meta_sidecar import write_graph_with_meta

    save_state_for_graph(root, current_hashes, graph)
    if content_unchanged and prior_graph_bytes is not None:
        graph_bytes, reused = prior_graph_bytes, True
    else:
        graph_bytes, reused = _canonical_on_disk_bytes(graph, prior_graph_bytes)
    persist_query_state_sidecar(root / ".weld", graph, graph_bytes=graph_bytes)
    if with_sqlite:
        persist_sqlite_sidecar(root / ".weld", graph, graph_bytes=graph_bytes)
    persist_file_index(root)
    if write_graph:
        # ``reused`` means graph_bytes are exactly the prior on-disk bytes,
        # so the graph.json body is already correct -- only the volatile
        # graph-meta sidecar needs refreshing. Pass that through so
        # write_graph_with_meta skips both the re-serialize and the
        # 14 MB body rewrite (bd 85tb.2).
        write_graph_with_meta(
            root / ".weld" / "graph.json", graph,
            on_disk_bytes=graph_bytes, body_matches_disk=reused,
        )
