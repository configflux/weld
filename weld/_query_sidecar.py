"""Persistent sidecar for eagerly-built query state (ADR 0031).

`build_query_state` in :mod:`weld.query_state` constructs the inverted
index, BM25 corpus, and structural-score table from the graph's nodes
and edges. Those structures are pure functions of the input and account
for ~34 s of the 26 s `wd query` cold time on a 100k-file synthetic
repo (ADR 0027 profiling). This module persists them to
``.weld/query_state.bin`` so the next cold process loads them instead
of rebuilding.

Trust boundary (ADR 0025): the sidecar lives inside ``.weld/``, same
trust as ``graph.json``. Pickle is acceptable here because anyone who
can drop a hostile ``query_state.bin`` can already drop a hostile
``graph.json``. The envelope (magic, format-version, weld-schema,
graph digest, counts) is checked before the payload is trusted, so a
mismatched or tampered sidecar is treated as a cache miss rather than
loaded.

The module is intentionally small and isolated so callers in
``weld/graph.py`` and ``weld/discover.py`` stay readable:

- :func:`read_sidecar` -- return a :class:`QueryState` if the sidecar
  is present, fresh, and parses; otherwise ``None``. Never raises.
- :func:`write_sidecar` -- write the sidecar atomically beside
  ``graph.json``. Best-effort: write failures are logged to stderr and
  swallowed so they never fail a discover or load.
"""

from __future__ import annotations

import hashlib
import pickle
import sys
from pathlib import Path

from weld.contract import SCHEMA_VERSION
from weld.query_state import QueryState
from weld.workspace_state import atomic_write_bytes

__all__ = [
    "SIDECAR_FILENAME",
    "load_query_state_for_graph",
    "read_sidecar",
    "write_sidecar",
    "write_sidecar_for_bytes",
]

#: File name written next to ``graph.json``.
SIDECAR_FILENAME = "query_state.bin"

#: Magic string that pins this file as a weld query-state sidecar.
_MAGIC = "weld-query-state"

#: Envelope format version. Bump on any change to the envelope shape or
#: the payload's expected keys. Older sidecars are then treated as
#: absent and rebuilt on the next cold load (ADR 0031 (c)).
_FORMAT_VERSION = 1


def _weld_schema_version() -> int:
    """Indirected so tests can monkey-patch a future schema version."""
    return SCHEMA_VERSION


def _sidecar_path(graph_path: Path) -> Path:
    return graph_path.parent / SIDECAR_FILENAME


def _hash_graph_bytes(graph_path: Path) -> str | None:
    """Return the sha256 of ``graph_path``'s bytes, or ``None`` on read failure."""
    try:
        digest = hashlib.sha256()
        # Stream in 1 MiB chunks so we never hold the whole file twice.
        with graph_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def read_sidecar(
    graph_path: Path,
    nodes: dict[str, dict],
    edges: list[dict],
) -> QueryState | None:
    """Return a fresh :class:`QueryState` from disk, or ``None`` on miss.

    A "miss" is any of: file absent, unpickle failure, missing/wrong
    magic, mismatched format/weld-schema version, mismatched
    ``graph.json`` digest, or mismatched node/edge counts. None of
    those raise; this function is total over its inputs (including
    corrupt files) so :func:`weld.graph.Graph.load` never crashes on a
    tampered cache.
    """
    sidecar_path = _sidecar_path(graph_path)
    if not sidecar_path.is_file():
        return None

    # Outer try blanket-catches anything pickle, the unpickled object,
    # or our envelope checks might raise. ADR 0031 (c) is explicit
    # that a corrupt sidecar is a miss, not a fatal error.
    try:
        raw = sidecar_path.read_bytes()
        envelope = pickle.loads(raw)
        if not _envelope_matches(envelope, graph_path, nodes, edges):
            return None
        payload = envelope["payload"]
        return QueryState(
            inverted_index=payload["inverted_index"],
            bm25=payload["bm25"],
            structural_scores=payload["structural_scores"],
            embedding_cache=_rebuild_embedding_cache(payload.get("has_enrichment")),
        )
    except Exception:  # noqa: BLE001 -- corrupt cache must never crash callers.
        return None


def _envelope_matches(
    envelope: object,
    graph_path: Path,
    nodes: dict[str, dict],
    edges: list[dict],
) -> bool:
    """Validate every field on the envelope before trusting the payload."""
    if not isinstance(envelope, dict):
        return False
    if envelope.get("magic") != _MAGIC:
        return False
    if envelope.get("format_version") != _FORMAT_VERSION:
        return False
    if envelope.get("weld_schema_version") != _weld_schema_version():
        return False
    if envelope.get("node_count") != len(nodes):
        return False
    if envelope.get("edge_count") != len(edges):
        return False
    expected_digest = _hash_graph_bytes(graph_path)
    if expected_digest is None or envelope.get("graph_sha256") != expected_digest:
        return False
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        return False
    for key in ("inverted_index", "bm25", "structural_scores"):
        if key not in payload:
            return False
    return True


def write_sidecar(
    graph_path: Path,
    nodes: dict[str, dict],
    edges: list[dict],
    state: QueryState,
) -> None:
    """Write ``query_state.bin`` atomically next to ``graph_path``.

    Best-effort: any write failure is logged to stderr and swallowed.
    A failed sidecar write must not fail discovery or query loading.
    """
    digest = _hash_graph_bytes(graph_path)
    if digest is None:
        # No graph.json on disk -> no point caching against a missing
        # baseline. A future load would treat the entry as stale anyway.
        return
    _write_sidecar_with_digest(graph_path.parent, digest, nodes, edges, state)


def write_sidecar_for_bytes(
    weld_dir: Path,
    graph_bytes: bytes,
    nodes: dict[str, dict],
    edges: list[dict],
    state: QueryState,
) -> None:
    """Write the sidecar using a digest computed from in-memory graph bytes.

    Used by the discovery pipeline so the sidecar can be written without
    waiting for the canonical ``graph.json`` write to land on disk: the
    bytes that *will* be written are the same bytes whose sha256 we
    record. Best-effort like :func:`write_sidecar`.
    """
    digest = hashlib.sha256(graph_bytes).hexdigest()
    _write_sidecar_with_digest(weld_dir, digest, nodes, edges, state)


def _write_sidecar_with_digest(
    weld_dir: Path,
    digest: str,
    nodes: dict[str, dict],
    edges: list[dict],
    state: QueryState,
) -> None:
    envelope = {
        "magic": _MAGIC,
        "format_version": _FORMAT_VERSION,
        "weld_schema_version": _weld_schema_version(),
        "graph_sha256": digest,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "payload": {
            "inverted_index": state.inverted_index,
            "bm25": state.bm25,
            "structural_scores": state.structural_scores,
            # We do not pickle the embedding cache itself: it is built
            # lazily from node descriptions and does not benefit from
            # warm-loading. We do record whether enrichment is present
            # so the reader can recreate an empty cache.
            "has_enrichment": state.embedding_cache is not None,
        },
    }
    target = weld_dir / SIDECAR_FILENAME
    try:
        atomic_write_bytes(target, pickle.dumps(envelope, protocol=pickle.HIGHEST_PROTOCOL))
    except OSError as exc:
        # Best-effort cache: log and move on. ADR 0031 (e) -- the sidecar
        # is purely additive; missing it just means the next cold load
        # rebuilds.
        print(
            f"[weld] notice: failed to write query-state sidecar at "
            f"{target}: {exc}",
            file=sys.stderr,
        )


def _rebuild_embedding_cache(has_enrichment: object) -> object:
    """Recreate an empty embedding cache when the original graph had enrichment."""
    if not has_enrichment:
        return None
    from weld.embeddings import TextEmbeddingCache

    return TextEmbeddingCache()


def load_query_state_for_graph(graph: object) -> None:
    """Populate ``graph``'s query-state fields from the sidecar or rebuild.

    Hot path: a fresh sidecar replaces the in-memory inverted index,
    BM25 corpus, and structural scores without rebuilding. Cache miss:
    fall back to the in-memory rebuild via ``graph._build_inverted_index``
    and write a fresh sidecar so the next cold load hits.

    Lives here (next to the rest of the sidecar I/O) instead of on
    :class:`weld.graph.Graph` so ``graph.py`` stays under its
    line-count cap (ADR project policy) without giving up readability.
    """
    # Late binding: graph is a weld.graph.Graph; we touch its private
    # ``_path``, ``_data``, and the private query-state attributes.
    graph_path = graph._path  # type: ignore[attr-defined]
    nodes = graph._data.get("nodes", {})  # type: ignore[attr-defined]
    edges = graph._data.get("edges", [])  # type: ignore[attr-defined]

    cached = read_sidecar(graph_path, nodes, edges)
    if cached is not None:
        _apply_query_state(graph, cached)
        return

    # Cache miss: trigger the existing rebuild and persist its result.
    graph._build_inverted_index()  # type: ignore[attr-defined]
    snapshot = _snapshot_from_graph(graph)
    if snapshot is not None:
        write_sidecar(graph_path, nodes, edges, snapshot)


def _apply_query_state(graph: object, state: QueryState) -> None:
    graph._inverted_index = state.inverted_index  # type: ignore[attr-defined]
    graph._bm25 = state.bm25  # type: ignore[attr-defined]
    graph._structural_scores = state.structural_scores  # type: ignore[attr-defined]
    graph._embedding_cache = state.embedding_cache  # type: ignore[attr-defined]
    graph._query_state_counts = (  # type: ignore[attr-defined]
        len(graph._data["nodes"]),  # type: ignore[attr-defined]
        len(graph._data["edges"]),  # type: ignore[attr-defined]
    )
    # ADR 0041 alias index. Rebuilt from the freshly-loaded
    # ``nodes`` dict; not pickled into the sidecar because (a) it is
    # cheap (single linear pass) and (b) keeping it out of the
    # pickle envelope avoids a sidecar-format bump and the
    # corresponding cache-invalidation rule. The graph hash on the
    # sidecar already invalidates the cached inverted index when
    # ``graph.json`` changes; the alias index, being derived from
    # the same ``nodes`` dict, is implicitly fresh whenever the
    # cached state itself is fresh.
    from weld._alias_index import build_alias_index
    graph._alias_index = build_alias_index(graph._data["nodes"])  # type: ignore[attr-defined]


def _snapshot_from_graph(graph: object) -> QueryState | None:
    """Wrap the graph's already-built fields into a QueryState for writing."""
    bm25 = graph._bm25  # type: ignore[attr-defined]
    if bm25 is None:
        return None
    return QueryState(
        inverted_index=graph._inverted_index,  # type: ignore[attr-defined]
        bm25=bm25,
        structural_scores=graph._structural_scores,  # type: ignore[attr-defined]
        embedding_cache=graph._embedding_cache,  # type: ignore[attr-defined]
    )
