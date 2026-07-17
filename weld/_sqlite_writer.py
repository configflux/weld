"""SQLite sidecar writer (ADR 0058).

Builds ``.weld/graph.db`` from the canonical ``graph.json`` (or from an
already-loaded in-memory graph dict). The output is deterministic: same
JSON in, byte-identical sidecar out, modulo SQLite's internal btree
layout which is stable for stable insertion order.

The build path is:

1. Compute the SHA-256 of the canonical JSON bytes (the writer always
   pairs the sidecar with a specific JSON serialization, so the reader's
   freshness check is exact -- not a digest of in-memory state that
   could drift from what landed on disk).
2. Open a fresh sqlite database in a temp file beside the target.
3. Apply build-time pragmas and the DDL in
   :mod:`weld._sqlite_schema`.
4. Insert nodes in alphabetical-by-id order (ADR 0058 §Determinism).
5. Insert edges in ``(from_id, to_id, type, mint_edge_id)`` order.
6. Populate the inverted-index tables (ADR 0058 Option B): one row
   per (token, node_id, frequency) tuple, per-node length stats, and
   corpus-level BM25 stats. Mirrors ``weld.query_index.build_index``
   and ``weld.bm25.BM25Corpus.from_nodes``.
7. Stamp the ``meta`` table with the closed key set.
8. ``os.replace`` the temp file into place (POSIX rename atomicity --
   readers always see the old sidecar or the new one, never a half-built
   file).

Every write is via a parameterized ``executemany`` -- no string
formatting touches user-provided node ids, types, or props. ADR 0058
§Security and the security pass on this task both flag SQL injection
as the main risk class; binding parameters eliminates it. Path safety
is delegated to :func:`Path.resolve` plus the same-directory temp file
contract used elsewhere in weld (``atomic_write_text``).
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from weld._review import mint_edge_id
from weld._sqlite_index import insert_token_index
from weld._sqlite_schema import (
    CREATE_STATEMENTS,
    META_KEY_GENERATED_AT,
    META_KEY_SCHEMA_VERSION,
    META_KEY_SOURCE_JSON_SHA,
    META_KEY_SQLITE_SCHEMA_VERSION,
    META_KEY_WELD_VERSION,
    PRAGMAS_BUILD,
    SIDECAR_FILENAME,
    SQLITE_SCHEMA_VERSION,
)
from weld._notice import emit

__all__ = [
    "SIDECAR_FILENAME",
    "build_sidecar",
    "build_sidecar_for_bytes",
    "build_sidecar_from_graph_path",
    "compute_source_json_sha",
    "sidecar_path_for",
]


def sidecar_path_for(graph_json_path: Path) -> Path:
    """Return the sidecar path that pairs with *graph_json_path*."""
    return Path(graph_json_path).parent / SIDECAR_FILENAME


def compute_source_json_sha(graph_json_bytes: bytes) -> str:
    """Return the SHA-256 hex digest of *graph_json_bytes*.

    The writer and reader both speak this digest; the reader compares
    the on-disk JSON's bytes against the value the writer stamped. We
    use SHA-256 here rather than a shorter hash to stay aligned with
    the existing query-state sidecar (ADR 0031) and the federation
    digest in :mod:`weld.federation`.
    """
    # Hash kept local so the writer module's exports stay focused.
    import hashlib

    return hashlib.sha256(graph_json_bytes).hexdigest()


def _weld_version() -> str:
    """Return the installed weld version string, or ``"0"`` on failure.

    The sidecar is best-effort; an unavailable version (e.g. running
    from a partial checkout without metadata) must not fail the build.
    """
    try:
        from importlib.metadata import version

        return version("weld")
    except Exception:  # noqa: BLE001 -- version lookup is best-effort.
        try:
            version_file = Path(__file__).resolve().parent.parent / "VERSION"
            if version_file.is_file():
                return version_file.read_text(encoding="utf-8").strip() or "0"
        except OSError:
            pass
        return "0"


def _now_iso() -> str:
    """Return an ISO-8601 timestamp with second precision in UTC."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _node_row(node_id: str, node: dict) -> tuple[
    str, str, str, str | None, str | None, str | None, str,
]:
    """Project a node dict to its sqlite row tuple."""
    props = node.get("props") if isinstance(node.get("props"), dict) else {}
    file_val = props.get("file")
    origin_val = props.get("origin")
    confidence_val = props.get("confidence")
    return (
        str(node_id),
        str(node.get("type", "")),
        str(node.get("label", "")),
        str(file_val) if isinstance(file_val, str) else None,
        str(origin_val) if isinstance(origin_val, str) else None,
        str(confidence_val) if isinstance(confidence_val, str) else None,
        # ``sort_keys`` keeps props_json byte-stable across rebuilds even
        # if upstream code mutates dict insertion order; ``ensure_ascii``
        # is on so the same bytes appear regardless of locale.
        json.dumps(props, sort_keys=True, ensure_ascii=True),
    )


def _edge_row(edge: dict) -> tuple[
    str, str, str, str, str, str | None, str,
]:
    """Project an edge dict to its sqlite row tuple."""
    props = edge.get("props") if isinstance(edge.get("props"), dict) else {}
    source_strategy = props.get("source_strategy")
    confidence = props.get("confidence")
    return (
        mint_edge_id(edge),
        str(edge.get("from", "")),
        str(edge.get("to", "")),
        str(edge.get("type", "")),
        # ADR 0050 requires every edge to carry a confidence prop; the
        # writer tolerates a missing/non-string value by stamping an
        # empty string so the column stays NOT NULL. A reader that
        # cares can still introspect ``props_json``.
        str(confidence) if isinstance(confidence, str) else "",
        str(source_strategy) if isinstance(source_strategy, str) else None,
        json.dumps(props, sort_keys=True, ensure_ascii=True),
    )


def _sorted_node_items(nodes: dict[str, dict]) -> list[tuple[str, dict]]:
    """Return nodes as a list sorted alphabetically by id."""
    return sorted(nodes.items(), key=lambda kv: kv[0])


def _sorted_edges(edges: list[dict]) -> list[dict]:
    """Return edges sorted by (from, to, type, mint_edge_id).

    The mint_edge_id tiebreaker handles the rare case of two edges that
    share endpoints, type, and source_strategy but differ in other prop
    fields -- without it the insertion order would depend on which one
    Python's sort happened to encounter first.
    """
    def key(edge: dict) -> tuple[str, str, str, str]:
        return (
            str(edge.get("from", "")),
            str(edge.get("to", "")),
            str(edge.get("type", "")),
            mint_edge_id(edge),
        )

    return sorted(edges, key=key)


def _apply_pragmas(conn: sqlite3.Connection, pragmas: tuple[str, ...]) -> None:
    for pragma in pragmas:
        conn.execute(pragma)


def _apply_schema(conn: sqlite3.Connection) -> None:
    for statement in CREATE_STATEMENTS:
        conn.execute(statement)


def _stamp_meta(
    conn: sqlite3.Connection,
    *,
    schema_version: int,
    source_json_sha: str,
    generated_at: str,
    weld_version: str,
) -> None:
    rows = [
        (META_KEY_SCHEMA_VERSION, str(int(schema_version))),
        (META_KEY_SQLITE_SCHEMA_VERSION, str(int(SQLITE_SCHEMA_VERSION))),
        (META_KEY_SOURCE_JSON_SHA, str(source_json_sha)),
        (META_KEY_GENERATED_AT, str(generated_at)),
        (META_KEY_WELD_VERSION, str(weld_version)),
    ]
    conn.executemany("INSERT INTO meta(key, value) VALUES (?, ?)", rows)


def _resolve_temp_dir(target: Path) -> Path:
    """Pick the directory the temp file is created in.

    Same-directory temp files give us POSIX rename atomicity. The parent
    of *target* must exist; the caller is responsible for that via
    ``mkdir(parents=True, exist_ok=True)``.
    """
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    return parent


def build_sidecar(
    graph: dict,
    target_path: Path,
    *,
    source_json_sha: str,
    generated_at: str | None = None,
    weld_version: str | None = None,
) -> Path:
    """Build the sidecar from an in-memory *graph* dict.

    *graph* must follow the contract shape (``nodes`` dict + ``edges``
    list + ``meta``). *source_json_sha* is the SHA-256 of the canonical
    JSON bytes the caller will (or did) write; the reader compares
    this against the bytes it observes on disk to decide cache validity.

    Returns the resolved path of the written sidecar.
    """
    nodes = graph.get("nodes", {}) if isinstance(graph.get("nodes"), dict) else {}
    edges = graph.get("edges", []) if isinstance(graph.get("edges"), list) else []
    meta = graph.get("meta", {}) if isinstance(graph.get("meta"), dict) else {}
    schema_version = int(meta.get("schema_version", 1) or 1)

    target = Path(target_path).resolve()
    temp_dir = _resolve_temp_dir(target)

    # Use mkstemp so the half-built file lands beside the final path
    # (cross-device rename would not be atomic). Close the fd immediately;
    # sqlite3 reopens the path by name.
    fd, tmp_name = tempfile.mkstemp(
        prefix=f"{target.name}.tmp.", dir=str(temp_dir),
    )
    os.close(fd)
    tmp_path = Path(tmp_name)

    try:
        # sqlite3.connect against an empty file gets the same effect as
        # against a brand-new path -- mkstemp leaves a zero-byte file
        # which sqlite happily uses as a fresh database.
        conn = sqlite3.connect(str(tmp_path))
        try:
            _apply_pragmas(conn, PRAGMAS_BUILD)
            _apply_schema(conn)

            sorted_nodes = _sorted_node_items(nodes)
            node_rows = [_node_row(nid, node) for nid, node in sorted_nodes]
            edge_rows = [_edge_row(edge) for edge in _sorted_edges(edges)]

            conn.executemany(
                "INSERT INTO nodes(id, type, label, file, origin, confidence, props_json)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                node_rows,
            )
            conn.executemany(
                "INSERT INTO edges(id, from_id, to_id, type, confidence, source_strategy, props_json)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                edge_rows,
            )

            # ADR 0058 Option B: populate the inverted-index tables so
            # the federation query path can reconstruct just the rows
            # it needs without parsing graph.json.
            insert_token_index(conn, sorted_nodes)

            _stamp_meta(
                conn,
                schema_version=schema_version,
                source_json_sha=source_json_sha,
                generated_at=generated_at or _now_iso(),
                weld_version=weld_version or _weld_version(),
            )
            conn.commit()
        finally:
            conn.close()

        os.replace(str(tmp_path), str(target))
        return target
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def build_sidecar_for_bytes(
    graph: dict,
    graph_json_bytes: bytes,
    target_path: Path,
    *,
    generated_at: str | None = None,
    weld_version: str | None = None,
) -> Path:
    """Convenience wrapper that derives ``source_json_sha`` from bytes.

    Used by the discovery pipeline which already has the canonical JSON
    bytes in hand and wants the digest computed from them (not from
    whatever the on-disk file currently holds, which may race).
    """
    return build_sidecar(
        graph,
        target_path,
        source_json_sha=compute_source_json_sha(graph_json_bytes),
        generated_at=generated_at,
        weld_version=weld_version,
    )


def build_sidecar_from_graph_path(
    graph_json_path: Path,
    *,
    target_path: Path | None = None,
) -> Path:
    """Build the sidecar by reading and hashing ``graph.json`` from disk.

    Used by ``wd graph index --rebuild`` and by the doctor stale check
    when it offers to rebuild. The graph is parsed by the canonical
    schema loader so any forward-version mismatch is surfaced to the
    caller rather than silently producing a sidecar for a graph this
    build cannot understand.
    """
    from weld._graph_schema import load_graph_file

    graph_path = Path(graph_json_path).resolve()
    raw_bytes = graph_path.read_bytes()
    graph = load_graph_file(graph_path)
    target = (
        Path(target_path).resolve()
        if target_path is not None
        else sidecar_path_for(graph_path)
    )
    return build_sidecar(
        graph,
        target,
        source_json_sha=compute_source_json_sha(raw_bytes),
    )


def safe_build_sidecar_for_bytes(
    graph: dict,
    graph_json_bytes: bytes,
    target_path: Path,
) -> Path | None:
    """Best-effort build that swallows OSError / sqlite errors.

    Used by the discovery pipeline so that a sidecar-build hiccup never
    fails the canonical ``graph.json`` write. Any exception is logged
    to stderr and ``None`` is returned; the next ``Graph.open`` will
    then fall back to JSON (with the cost of a one-time index miss).
    """
    try:
        return build_sidecar_for_bytes(graph, graph_json_bytes, target_path)
    except (OSError, sqlite3.Error) as exc:
        emit(
            f"[weld] notice: failed to write sqlite sidecar at {target_path}: {exc}"
        )
        return None
