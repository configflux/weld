"""SQLite sidecar reader (ADR 0058).

The reader opens ``.weld/graph.db`` and validates the sidecar against
its paired ``graph.json``. Validation is strict: any mismatch (missing
file, wrong magic, wrong schema version, wrong source-JSON SHA, parse
failure) downgrades to ``None`` so the caller falls back to JSON. A
stale sidecar is a *cache miss*, not a fatal error -- ADR 0058 makes
this explicit and the security review on this change reaffirmed it
(we do not want a tampered or stale sidecar to surface data that
diverges from the canonical JSON the operator just wrote).

The :class:`SqliteBackedGraph` view exposes the same Python surface as
the JSON-backed :class:`weld.graph.Graph` for the read-only queries
documented in ADR 0058 §"Read path". It is intentionally narrow today;
the in-memory ``Graph`` keeps full ownership of mutations, file-system
state, query state, and embedding cache. The sidecar is a *cache*, not
a replacement.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from weld._sqlite_schema import (
    META_KEY_SCHEMA_VERSION,
    META_KEY_SOURCE_JSON_SHA,
    META_KEY_SQLITE_SCHEMA_VERSION,
    PRAGMAS_READ,
    SIDECAR_FILENAME,
    SQLITE_SCHEMA_VERSION,
)

__all__ = [
    "SIDECAR_FILENAME",
    "SqliteBackedGraph",
    "open_sidecar_if_fresh",
    "read_meta",
    "sidecar_freshness",
]


def _hash_graph_bytes(graph_path: Path) -> str | None:
    """Return the sha256 of ``graph_path``'s bytes, or ``None`` on error.

    Streamed in 1 MiB chunks so callers never hold the JSON twice in
    memory; mirrors the helper in :mod:`weld._query_sidecar`.
    """
    try:
        digest = hashlib.sha256()
        with graph_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _connect_read_only(db_path: Path) -> sqlite3.Connection | None:
    """Return a read-only connection or ``None`` on any open failure.

    Uses the URI form so we can pin ``mode=ro``. The path is
    percent-encoded so user-supplied directories containing URI-reserved
    characters (``?``, ``#``, fragment markers) cannot bleed into the
    URI's query string. The connection has ``query_only`` applied as a
    belt-and-braces guard: a write attempt over a read-only connection
    already fails, but a future refactor that drops the URI mode flag
    would still trip ``query_only``.
    """
    try:
        from urllib.parse import quote

        # ``safe="/"`` keeps directory separators readable; everything
        # else is percent-encoded so reserved URI chars in the user's
        # project path cannot escape the path component.
        encoded = quote(db_path.as_posix(), safe="/")
        uri = f"file:{encoded}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        for pragma in PRAGMAS_READ:
            conn.execute(pragma)
        return conn
    except sqlite3.Error:
        return None


def read_meta(conn: sqlite3.Connection) -> dict[str, str]:
    """Return the meta table contents as a flat key-value dict.

    Skips any non-string values (defensive; the writer always inserts
    strings, but a hand-modified sidecar should not crash the reader).
    """
    out: dict[str, str] = {}
    for row in conn.execute("SELECT key, value FROM meta"):
        key, value = row[0], row[1]
        if isinstance(key, str) and isinstance(value, str):
            out[key] = value
    return out


def sidecar_freshness(graph_json_path: Path) -> tuple[bool, dict[str, str]]:
    """Return ``(fresh, meta)`` for the sidecar paired with *graph_json_path*.

    A sidecar is considered fresh when:

    - it exists at ``<graph_dir>/graph.db``;
    - it parses as a sqlite database;
    - its ``meta.sqlite_schema_version`` equals
      :data:`SQLITE_SCHEMA_VERSION`;
    - its ``meta.source_json_sha`` equals the sha256 of the on-disk
      JSON bytes.

    Any other condition collapses to ``(False, {})``. Used by
    :class:`weld.graph.Graph.open` (cache validity gate) and by
    ``wd doctor`` (stale-sidecar warning).
    """
    graph_path = Path(graph_json_path)
    if not graph_path.is_file():
        return False, {}
    db_path = graph_path.parent / SIDECAR_FILENAME
    if not db_path.is_file():
        return False, {}

    conn = _connect_read_only(db_path)
    if conn is None:
        return False, {}
    try:
        try:
            meta = read_meta(conn)
        except sqlite3.Error:
            return False, {}
    finally:
        conn.close()

    try:
        observed_schema = int(meta.get(META_KEY_SQLITE_SCHEMA_VERSION, "0"))
    except (TypeError, ValueError):
        return False, meta
    if observed_schema != SQLITE_SCHEMA_VERSION:
        return False, meta

    expected_sha = meta.get(META_KEY_SOURCE_JSON_SHA, "")
    if not expected_sha:
        return False, meta
    observed_sha = _hash_graph_bytes(graph_path)
    if observed_sha is None or observed_sha != expected_sha:
        return False, meta

    return True, meta


def open_sidecar_if_fresh(graph_json_path: Path) -> SqliteBackedGraph | None:
    """Return a read-only view if the sidecar is fresh, otherwise ``None``.

    Caller (typically :meth:`weld.graph.Graph.open`) treats ``None`` as
    "fall back to JSON". This function never raises on a broken
    sidecar; it just returns ``None`` so the canonical JSON path always
    works.
    """
    fresh, meta = sidecar_freshness(graph_json_path)
    if not fresh:
        return None
    db_path = Path(graph_json_path).parent / SIDECAR_FILENAME
    conn = _connect_read_only(db_path)
    if conn is None:
        return None
    return SqliteBackedGraph(conn, meta, graph_json_path=Path(graph_json_path))


class SqliteBackedGraph:
    """Read-only sqlite-backed view of a single repo's graph.

    Mirrors the subset of :class:`weld.graph.Graph` that federation
    uses: id lookups, type filters, neighbor fan-out, a lazy
    inverted-index ``query`` (ADR 0058 Option B), and a ``dump()``
    adapter. Mutations belong on the JSON-backed :class:`Graph`.
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        meta: dict[str, str],
        *,
        graph_json_path: Path,
    ) -> None:
        self._conn = connection
        self._meta = dict(meta)
        self._graph_json_path = Path(graph_json_path)
        # Cached counts to keep stats cheap; lazily computed.
        self._node_count: int | None = None
        self._edge_count: int | None = None

    # -- lifecycle -----------------------------------------------------

    def close(self) -> None:
        """Close the underlying connection. Idempotent."""
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

    def __enter__(self) -> SqliteBackedGraph:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    # -- meta ----------------------------------------------------------

    @property
    def meta(self) -> dict[str, str]:
        """Read-only copy of the sidecar's meta table."""
        return dict(self._meta)

    @property
    def schema_version(self) -> int:
        try:
            return int(self._meta.get(META_KEY_SCHEMA_VERSION, "1"))
        except (TypeError, ValueError):
            return 1

    @property
    def source_json_sha(self) -> str:
        return self._meta.get(META_KEY_SOURCE_JSON_SHA, "")

    @property
    def graph_json_path(self) -> Path:
        return self._graph_json_path

    # -- counts --------------------------------------------------------

    def node_count(self) -> int:
        if self._node_count is None:
            row = self._conn.execute("SELECT COUNT(*) FROM nodes").fetchone()
            self._node_count = int(row[0]) if row else 0
        return self._node_count

    def edge_count(self) -> int:
        if self._edge_count is None:
            row = self._conn.execute("SELECT COUNT(*) FROM edges").fetchone()
            self._edge_count = int(row[0]) if row else 0
        return self._edge_count

    # -- node queries --------------------------------------------------

    def get_node(self, node_id: str) -> dict | None:
        """Return the JSON-shaped node dict for *node_id*, or ``None``."""
        row = self._conn.execute(
            "SELECT id, type, label, props_json FROM nodes WHERE id = ? LIMIT 1",
            (str(node_id),),
        ).fetchone()
        if row is None:
            return None
        return _row_to_node(row)

    def iter_nodes(
        self, *, type_filter: str | None = None,
    ) -> Iterator[dict]:
        """Yield nodes lazily, optionally filtered by type."""
        if type_filter is None:
            cursor = self._conn.execute(
                "SELECT id, type, label, props_json FROM nodes ORDER BY id",
            )
        else:
            cursor = self._conn.execute(
                "SELECT id, type, label, props_json FROM nodes"
                " WHERE type = ? ORDER BY id",
                (str(type_filter),),
            )
        for row in cursor:
            yield _row_to_node(row)

    def list_nodes(self, type_filter: str | None = None) -> list[dict]:
        """List all nodes (parity with :meth:`Graph.list_nodes`)."""
        return list(self.iter_nodes(type_filter=type_filter))

    # -- edge queries --------------------------------------------------

    def iter_edges(self) -> Iterator[dict]:
        """Yield every edge in deterministic order."""
        cursor = self._conn.execute(
            "SELECT from_id, to_id, type, props_json FROM edges"
            " ORDER BY from_id, to_id, type, id",
        )
        for row in cursor:
            yield _row_to_edge(row)

    def edges_from(self, node_id: str) -> list[dict]:
        cursor = self._conn.execute(
            "SELECT from_id, to_id, type, props_json FROM edges"
            " WHERE from_id = ? ORDER BY to_id, type, id",
            (str(node_id),),
        )
        return [_row_to_edge(row) for row in cursor]

    def edges_to(self, node_id: str) -> list[dict]:
        cursor = self._conn.execute(
            "SELECT from_id, to_id, type, props_json FROM edges"
            " WHERE to_id = ? ORDER BY from_id, type, id",
            (str(node_id),),
        )
        return [_row_to_edge(row) for row in cursor]

    def neighbors(self, node_id: str) -> list[dict]:
        """Return edges touching *node_id* (either direction)."""
        cursor = self._conn.execute(
            "SELECT from_id, to_id, type, props_json FROM edges"
            " WHERE from_id = ? OR to_id = ?"
            " ORDER BY from_id, to_id, type, id",
            (str(node_id), str(node_id)),
        )
        return [_row_to_edge(row) for row in cursor]

    def query(self, term: str, limit: int = 20) -> dict:
        """Lazy per-query inverted-index lookup (ADR 0058 Option B).

        Returns the :func:`weld.graph_query.query_graph` envelope shape;
        the federation wrapper fills ``neighbors`` / ``edges``.
        """
        from weld._sqlite_query import query_sqlite_backed  # lazy import
        return query_sqlite_backed(self._conn, self, term, limit=limit)

    # -- materialisation ----------------------------------------------

    def dump(self) -> dict[str, Any]:
        """Materialise the full graph dict (nodes + edges + meta).

        Escape hatch for JSON-shaped callers; defeats sidecar laziness,
        so prefer iterator methods.
        """
        nodes: dict[str, dict] = {}
        for node in self.iter_nodes():
            nid = node.pop("id")
            nodes[nid] = node
        edges = list(self.iter_edges())
        return {
            "meta": {
                "schema_version": self.schema_version,
                "sqlite_source_json_sha": self.source_json_sha,
            },
            "nodes": nodes,
            "edges": edges,
        }


def _safe_props(props_json: object) -> dict:
    """Parse a ``props_json`` text column. Tolerant of garbage."""
    if not isinstance(props_json, str):
        return {}
    try:
        loaded = json.loads(props_json)
    except (ValueError, TypeError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    return loaded


def _row_to_node(row: tuple) -> dict:
    """Reshape ``(id, type, label, props_json)`` -> JSON-style node dict."""
    return {
        "id": row[0],
        "type": row[1],
        "label": row[2],
        "props": _safe_props(row[3]),
    }


def _row_to_edge(row: tuple) -> dict:
    """Reshape ``(from_id, to_id, type, props_json)`` -> JSON-style edge dict."""
    return {
        "from": row[0],
        "to": row[1],
        "type": row[2],
        "props": _safe_props(row[3]),
    }


def warn_stale_sidecar(graph_json_path: Path) -> None:
    """Print a stderr notice when the sidecar exists but is stale.

    Used by ``wd doctor`` and other diagnostic surfaces that want to
    surface drift without forcing a rebuild.
    """
    db_path = Path(graph_json_path).parent / SIDECAR_FILENAME
    if not db_path.is_file():
        return
    fresh, _meta = sidecar_freshness(graph_json_path)
    if fresh:
        return
    print(
        f"[weld] notice: {db_path} is stale (source_json_sha mismatch);"
        " run `wd graph index --rebuild` to refresh.",
        file=sys.stderr,
    )
