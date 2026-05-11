"""SQLite sidecar schema for the connected structure graph (ADR 0058).

The sidecar lives at ``.weld/graph.db`` next to ``.weld/graph.json``. The
JSON is canonical (ADR 0011 federation contract, ADR 0019 atomic write,
ADR 0012 determinism); the sqlite file is a derived index that lets
federation reads avoid loading every child's JSON into RAM.

This module owns:

- :data:`SIDECAR_FILENAME` -- the on-disk file name.
- :data:`SQLITE_SCHEMA_VERSION` -- envelope/format version stamped into
  ``meta.sqlite_schema_version``. Bump on any schema-shape change.
- :data:`CREATE_STATEMENTS` -- DDL strings applied at build time. The
  list is intentionally short and parameter-free; nothing here interpolates
  user input. SQL with bound values lives in :mod:`weld._sqlite_writer`
  and :mod:`weld._sqlite_reader`.
- :data:`META_KEYS` -- the closed set of keys the writer stamps into the
  ``meta`` table. The reader treats any unexpected key as ignorable noise
  so future versions stay forward-compatible by addition (ADR 0012 §3
  pattern).

Splitting the schema constants into a tiny helper module mirrors the
existing ``weld._graph_schema`` split and keeps the writer / reader files
under the 400-line CLAUDE.md cap.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "SIDECAR_FILENAME",
    "SQLITE_SCHEMA_VERSION",
    "CREATE_STATEMENTS",
    "META_KEY_SCHEMA_VERSION",
    "META_KEY_SQLITE_SCHEMA_VERSION",
    "META_KEY_SOURCE_JSON_SHA",
    "META_KEY_GENERATED_AT",
    "META_KEY_WELD_VERSION",
    "META_KEYS",
    "PRAGMAS_BUILD",
    "PRAGMAS_READ",
]

#: File name written next to ``graph.json``.
SIDECAR_FILENAME: Final[str] = "graph.db"

#: Envelope format version stamped into ``meta.sqlite_schema_version``.
#: Bump on any schema-shape change (column add/remove, index change,
#: meta-key add/remove). A reader that sees a higher version than it
#: understands treats the sidecar as stale and falls back to JSON; a
#: reader that sees the same version trusts the layout.
#:
#: Version history:
#:   1 -- Initial sidecar (meta, nodes, edges, hot-path indexes).
#:   2 -- ADR 0058 Option B: adds token_index, token_doc_lengths,
#:        token_field_stats so federation query can read inverted-index
#:        rows lazily per token instead of forcing a JSON parse to
#:        rebuild the index in memory.
SQLITE_SCHEMA_VERSION: Final[int] = 2

#: Meta keys the writer stamps. The reader looks these up by exact name;
#: any other key in the meta table is forward-compat noise.
META_KEY_SCHEMA_VERSION: Final[str] = "schema_version"
META_KEY_SQLITE_SCHEMA_VERSION: Final[str] = "sqlite_schema_version"
META_KEY_SOURCE_JSON_SHA: Final[str] = "source_json_sha"
META_KEY_GENERATED_AT: Final[str] = "generated_at"
META_KEY_WELD_VERSION: Final[str] = "weld_version"

META_KEYS: Final[tuple[str, ...]] = (
    META_KEY_SCHEMA_VERSION,
    META_KEY_SQLITE_SCHEMA_VERSION,
    META_KEY_SOURCE_JSON_SHA,
    META_KEY_GENERATED_AT,
    META_KEY_WELD_VERSION,
)

#: DDL applied during build, in order. Every statement is a literal --
#: no parameters, no user input. The CREATE TABLE statements model the
#: contract documented in ADR 0058 §"What sqlite stores": columned
#: hot-path fields plus an opaque ``props_json`` blob for the tail.
#:
#: Indexes are kept minimal: enough to make the federation queries
#: documented in ADR 0058 indexed but not so many that build time
#: balloons. A future version that needs another index bumps
#: :data:`SQLITE_SCHEMA_VERSION` and adds it here.
CREATE_STATEMENTS: Final[tuple[str, ...]] = (
    """
    CREATE TABLE meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE nodes (
        id TEXT PRIMARY KEY,
        type TEXT NOT NULL,
        label TEXT NOT NULL,
        file TEXT,
        origin TEXT,
        confidence TEXT,
        props_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX nodes_type_idx ON nodes(type)",
    "CREATE INDEX nodes_file_idx ON nodes(file) WHERE file IS NOT NULL",
    "CREATE INDEX nodes_origin_idx ON nodes(origin) WHERE origin IS NOT NULL",
    """
    CREATE TABLE edges (
        id TEXT PRIMARY KEY,
        from_id TEXT NOT NULL,
        to_id TEXT NOT NULL,
        type TEXT NOT NULL,
        confidence TEXT NOT NULL,
        source_strategy TEXT,
        props_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX edges_from_idx ON edges(from_id)",
    "CREATE INDEX edges_to_idx ON edges(to_id)",
    "CREATE INDEX edges_type_idx ON edges(type)",
    "CREATE INDEX edges_conf_idx ON edges(confidence)",
    # ADR 0058 Option B: lazy per-query inverted-index storage. The
    # ``token_index`` table mirrors what :func:`weld.query_index.build_index`
    # produces in memory -- one row per (indexed_token, node_id) pair.
    # ``frequency`` records the term-frequency of the token inside that
    # node's queryable surface; it is used to reconstruct the per-node
    # ``Counter`` that :class:`weld.bm25.BM25Corpus` keeps in memory.
    """
    CREATE TABLE token_index (
        token TEXT NOT NULL,
        node_id TEXT NOT NULL,
        frequency INTEGER NOT NULL
    )
    """,
    # Substring search uses ``LIKE token || '%'`` on this index. The
    # index also covers exact-token lookups (the planner picks the
    # narrowest LIKE-prefix range).
    "CREATE INDEX token_index_token_idx ON token_index(token)",
    "CREATE INDEX token_index_node_idx ON token_index(node_id)",
    # Per-node document length (sum of token frequencies) for BM25
    # length normalization. Computed once at write time so the reader
    # does not have to fan-out across token_index to total a node.
    """
    CREATE TABLE token_doc_lengths (
        node_id TEXT PRIMARY KEY,
        length INTEGER NOT NULL
    )
    """,
    # Corpus-level statistics for BM25 IDF. Today we store one row
    # keyed by a sentinel ``field`` value (``"_corpus"``) so the schema
    # can grow per-field stats later without a version bump.
    """
    CREATE TABLE token_field_stats (
        field TEXT PRIMARY KEY,
        avg_length REAL NOT NULL,
        total_docs INTEGER NOT NULL
    )
    """,
)

#: Pragmas applied at build time. ``journal_mode = MEMORY`` and
#: ``synchronous = OFF`` are safe because the entire build runs against
#: a fresh temp file that is then renamed into place atomically -- if the
#: process dies mid-build we throw away the temp file (no corruption of
#: the previously-valid sidecar). ``temp_store = MEMORY`` avoids leaking
#: temp files into the build directory.
PRAGMAS_BUILD: Final[tuple[str, ...]] = (
    "PRAGMA journal_mode = MEMORY",
    "PRAGMA synchronous = OFF",
    "PRAGMA temp_store = MEMORY",
)

#: Pragmas applied at read time. ``query_only = ON`` prevents any
#: accidental write through a reader connection; the sidecar is a cache
#: and writes go through :mod:`weld._sqlite_writer` only.
PRAGMAS_READ: Final[tuple[str, ...]] = (
    "PRAGMA query_only = ON",
)
