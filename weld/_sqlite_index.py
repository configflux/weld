"""SQLite inverted-index helpers (ADR 0058 Option B).

This module owns the read/write surface for the three inverted-index
tables added by sqlite schema version 2 (``token_index``,
``token_doc_lengths``, ``token_field_stats``). The writer module
(:mod:`weld._sqlite_writer`) calls into the build helpers at sidecar
build time; the federation query path (:class:`weld._sqlite_reader.
SqliteBackedGraph` and :class:`weld.federation.FederatedGraph`) calls
into the read helpers per query.

Splitting these out keeps both the writer and reader modules under the
project line-count cap and gives the query path a single place to
reason about token-row hydration.

Security note: every read helper here is parameter-bound. The
substring search uses ``LIKE ? || '%'`` plus a separate
``token == ?`` exact-match path; the wildcard never accepts unescaped
user input. No code path in this module concatenates a query term into
SQL text.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from typing import Iterable

from weld.query_index import node_tokens

__all__ = [
    "CORPUS_STATS_FIELD",
    "TokenIndexBuildResult",
    "build_token_rows",
    "insert_token_index",
    "read_token_rows_for_token",
    "read_doc_lengths",
    "read_corpus_stats",
    "read_node_frequencies",
]


#: Sentinel field-name stored in ``token_field_stats`` for the corpus-wide
#: BM25 stats. Keeping the schema field-keyed (rather than introducing a
#: dedicated singleton table) lets a future writer record per-field stats
#: without bumping the sidecar schema version again.
CORPUS_STATS_FIELD: str = "_corpus"


class TokenIndexBuildResult:
    """Computed inverted-index rows ready for bulk insert.

    Bundled instead of returned as a tuple so future fields (e.g.
    per-type stats) can extend the contract without a callsite churn.
    """

    __slots__ = ("token_rows", "doc_length_rows", "avg_length", "doc_count")

    def __init__(
        self,
        token_rows: list[tuple[str, str, int]],
        doc_length_rows: list[tuple[str, int]],
        avg_length: float,
        doc_count: int,
    ) -> None:
        self.token_rows = token_rows
        self.doc_length_rows = doc_length_rows
        self.avg_length = avg_length
        self.doc_count = doc_count


def build_token_rows(
    sorted_nodes: Iterable[tuple[str, dict]],
) -> TokenIndexBuildResult:
    """Compute the row stream for the three inverted-index tables.

    Mirrors :func:`weld.query_index.build_index` (token sourcing) and
    :class:`weld.bm25.BM25Corpus.from_nodes` (per-doc length and avg).
    Deterministic when *sorted_nodes* is sorted by id and the per-node
    Counter is iterated in sorted-key order: identical inputs in,
    identical row stream out.
    """
    token_rows: list[tuple[str, str, int]] = []
    doc_length_rows: list[tuple[str, int]] = []
    total_length = 0
    doc_count = 0

    for node_id, node in sorted_nodes:
        doc_count += 1
        tokens = node_tokens(node_id, node)
        frequencies = Counter(tokens)
        length = sum(frequencies.values())
        total_length += length
        doc_length_rows.append((node_id, int(length)))
        # Sort by token so the row stream is deterministic across runs
        # even if the underlying Counter iteration order shifts.
        for token in sorted(frequencies.keys()):
            token_rows.append((token, node_id, int(frequencies[token])))

    avg_length = total_length / doc_count if doc_count else 0.0
    return TokenIndexBuildResult(
        token_rows=token_rows,
        doc_length_rows=doc_length_rows,
        avg_length=avg_length,
        doc_count=doc_count,
    )


def insert_token_index(
    conn: sqlite3.Connection,
    sorted_nodes: list[tuple[str, dict]],
) -> None:
    """Populate the three Option B inverted-index tables.

    Always writes the corpus stats row, even on an empty graph; a
    missing row means the writer ran against a v1 schema (not an
    empty corpus), and the reader uses that to detect a mid-rollout
    sidecar.
    """
    result = build_token_rows(sorted_nodes)
    if result.token_rows:
        conn.executemany(
            "INSERT INTO token_index(token, node_id, frequency) VALUES (?, ?, ?)",
            result.token_rows,
        )
    if result.doc_length_rows:
        conn.executemany(
            "INSERT INTO token_doc_lengths(node_id, length) VALUES (?, ?)",
            result.doc_length_rows,
        )
    conn.execute(
        "INSERT INTO token_field_stats(field, avg_length, total_docs) VALUES (?, ?, ?)",
        (CORPUS_STATS_FIELD, float(result.avg_length), int(result.doc_count)),
    )


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------


def read_token_rows_for_token(
    conn: sqlite3.Connection,
    token: str,
) -> list[tuple[str, str, int]]:
    """Return ``(token, node_id, frequency)`` rows touched by *token*.

    Matches the substring semantics of the in-memory
    :func:`weld.query_index.candidate_nodes`: an indexed token is
    "hit" when the query token appears as a substring of the indexed
    token. The sqlite query implements that with a parameter-bound
    ``LIKE '%' || ? || '%'`` -- the term itself is never interpolated
    into SQL text. SQLite LIKE wildcards inside *token* (``%`` and
    ``_``) are matched literally by escaping them with ``\\``.

    Returns an empty list for an empty token; callers do not need to
    filter empties themselves.
    """
    if not token:
        return []
    safe_token = _escape_like(token)
    cursor = conn.execute(
        "SELECT token, node_id, frequency FROM token_index"
        " WHERE token LIKE '%' || ? || '%' ESCAPE '\\'",
        (safe_token,),
    )
    return [(row[0], row[1], int(row[2])) for row in cursor]


def read_doc_lengths(
    conn: sqlite3.Connection,
    node_ids: Iterable[str],
) -> dict[str, int]:
    """Return ``{node_id: length}`` for the supplied ids.

    Uses a single ``IN (?, ?, ...)`` parameterized statement so the
    query never interpolates ids into SQL text. SQLite's parameter
    limit (``SQLITE_MAX_VARIABLE_NUMBER`` -- conservatively 999) is
    handled by chunking inside the helper so callers can pass arbitrary
    id lists without worrying about the limit.
    """
    ids = list(node_ids)
    if not ids:
        return {}
    lengths: dict[str, int] = {}
    for chunk in _chunk(ids, 900):
        placeholders = ",".join("?" for _ in chunk)
        query = f"SELECT node_id, length FROM token_doc_lengths WHERE node_id IN ({placeholders})"
        for row in conn.execute(query, chunk):
            lengths[row[0]] = int(row[1])
    return lengths


def read_corpus_stats(conn: sqlite3.Connection) -> tuple[float, int]:
    """Return ``(avg_length, total_docs)`` for the corpus.

    Returns ``(0.0, 0)`` when no row is present. The reader treats that
    as "no inverted index in this sidecar" and the federation path
    falls back to JSON; that is exactly the v1-sidecar contract.
    """
    row = conn.execute(
        "SELECT avg_length, total_docs FROM token_field_stats WHERE field = ?",
        (CORPUS_STATS_FIELD,),
    ).fetchone()
    if row is None:
        return 0.0, 0
    return float(row[0]), int(row[1])


def read_node_frequencies(
    conn: sqlite3.Connection,
    node_ids: Iterable[str],
) -> dict[str, dict[str, int]]:
    """Return ``{node_id: {token: frequency}}`` for the supplied ids.

    Used by the BM25 scoring path: every candidate node needs its
    per-token frequency map to compute a score. The query is chunked
    on ``node_ids`` to stay under SQLite's variable-number limit.
    """
    ids = list(node_ids)
    if not ids:
        return {}
    by_node: dict[str, dict[str, int]] = {}
    for chunk in _chunk(ids, 900):
        placeholders = ",".join("?" for _ in chunk)
        query = (
            "SELECT node_id, token, frequency FROM token_index"
            f" WHERE node_id IN ({placeholders})"
        )
        for row in conn.execute(query, chunk):
            node_id, token, freq = row[0], row[1], int(row[2])
            by_node.setdefault(node_id, {})[token] = freq
    return by_node


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _escape_like(value: str) -> str:
    """Escape LIKE wildcards (``%``, ``_``, ``\\``) so they match literally."""
    return (
        value.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def _chunk(items: list[str], size: int) -> Iterable[list[str]]:
    """Yield *items* in fixed-size chunks."""
    for start in range(0, len(items), size):
        yield items[start:start + size]
