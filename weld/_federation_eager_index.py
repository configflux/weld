"""Eager federation inverted-index aggregation (ADR 0063).

Opt-in cache: walks every fresh-sidecar child's index tables once at
federation construction time and serves :meth:`FederatedGraph.query`
from in-memory dicts instead of per-query sqlite reads. Strict
superset of the lazy default: stale/missing-sidecar children keep the
per-query fallback. Match-set parity with
:func:`weld._sqlite_query.query_sqlite_backed` is the contract; the
federation correctness test asserts it.
"""

from __future__ import annotations

import math
import os
import sqlite3
from collections import Counter
from typing import Iterable

from weld._sqlite_query import _BM25_B, _BM25_K1
from weld._sqlite_reader import SqliteBackedGraph
from weld.ranking import (
    authority_score,
    confidence_score,
    exact_symbol_match_rank,
    resolution_penalty,
)
from weld.synonyms import expand_token_groups

__all__ = [
    "EAGER_ENV_VAR",
    "EagerFederationIndex",
    "build_eager_index_for",
    "env_var_truthy",
    "resolve_eager_flag",
]

#: Env var: ``WELD_FEDERATION_EAGER=1`` (or any truthy) flips eager
#: on for any FederatedGraph constructed without an explicit
#: ``eager_index=`` argument.
EAGER_ENV_VAR = "WELD_FEDERATION_EAGER"

#: Documented truthy values (case-folded); anything else means off.
_TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})


def env_var_truthy(value: str | None) -> bool:
    """Return True iff *value* is one of the documented truthy strings."""
    if value is None:
        return False
    return value.strip().lower() in _TRUTHY_VALUES


def resolve_eager_flag(explicit: bool | None) -> bool:
    """Constructor arg wins; ``None`` consults ``WELD_FEDERATION_EAGER``."""
    if explicit is not None:
        return bool(explicit)
    return env_var_truthy(os.environ.get(EAGER_ENV_VAR))


class EagerFederationIndex:
    """Aggregated inverted index over all sqlite-fresh children.

    Built once at construction; read-only afterwards. The
    ``eager_children`` set names the children covered; the federation
    falls back to the lazy/JSON path for any name not in that set.
    """

    __slots__ = (
        "inverted",
        "doc_lengths",
        "avg_length",
        "total_docs",
        "eager_children",
        # Per-child precomputations so query_child_matches does not
        # walk the federated dicts on every call.
        "_per_child_token_to_nodes",
        "_per_child_node_freq",
    )

    def __init__(
        self,
        *,
        inverted: dict[str, list[tuple[str, str, int]]],
        doc_lengths: dict[tuple[str, str], int],
        avg_length: float,
        total_docs: int,
        eager_children: frozenset[str],
    ) -> None:
        # token -> [(child_name, local_node_id, frequency), ...]
        self.inverted = inverted
        # (child_name, local_node_id) -> token-length
        self.doc_lengths = doc_lengths
        self.avg_length = avg_length
        self.total_docs = total_docs
        self.eager_children = eager_children
        # Build the per-child views once; query_child_matches uses them.
        self._per_child_token_to_nodes: dict[str, dict[str, set[str]]] = {}
        self._per_child_node_freq: dict[str, dict[str, dict[str, int]]] = {}
        for token, hits in inverted.items():
            for cname, local_id, freq in hits:
                self._per_child_token_to_nodes.setdefault(
                    cname, {},
                ).setdefault(token, set()).add(local_id)
                self._per_child_node_freq.setdefault(
                    cname, {},
                ).setdefault(local_id, {})[token] = freq

    @classmethod
    def empty(cls) -> EagerFederationIndex:
        return cls(
            inverted={},
            doc_lengths={},
            avg_length=0.0,
            total_docs=0,
            eager_children=frozenset(),
        )

    @classmethod
    def build(
        cls,
        sqlite_children: Iterable[tuple[str, SqliteBackedGraph]],
    ) -> EagerFederationIndex:
        """Aggregate inverted-index rows from every sqlite-fresh child.

        ``sqlite_children`` is an iterable of ``(name, handle)`` tuples
        the federation supplies (typically the iteration over its
        ``_load_child(name)`` result filtered to ``SqliteBackedGraph``
        instances). Children whose handle is not a
        :class:`SqliteBackedGraph` are skipped silently; the federation
        still serves them via the lazy/JSON fallback.
        """
        inverted: dict[str, list[tuple[str, str, int]]] = {}
        doc_lengths: dict[tuple[str, str], int] = {}
        names: list[str] = []

        for name, handle in sqlite_children:
            if not isinstance(handle, SqliteBackedGraph):
                continue
            conn = handle._conn  # noqa: SLF001 -- module-internal contract.
            try:
                _aggregate_one_child(name, conn, inverted, doc_lengths)
            except sqlite3.Error:
                # Treat a mid-aggregation sqlite error like a stale sidecar:
                # skip from the eager index, fall back per-query.
                continue
            names.append(name)

        doc_count = len(doc_lengths)
        avg_length = (
            sum(doc_lengths.values()) / doc_count if doc_count else 0.0
        )
        return cls(
            inverted=inverted,
            doc_lengths=doc_lengths,
            avg_length=avg_length,
            total_docs=doc_count,
            eager_children=frozenset(names),
        )

    def query_child_matches(
        self,
        graph_view: SqliteBackedGraph,
        child_name: str,
        term: str,
        *,
        limit: int,
    ) -> list[dict]:
        """Return ranked matches for one child from the eager index.

        Envelope shape parity with
        :func:`weld._sqlite_query.query_sqlite_backed`.
        """
        tokens = term.lower().split()
        if not tokens:
            return []
        token_groups = expand_token_groups(tokens)

        candidates = self._candidates_strict_and(token_groups, child_name)
        if not candidates:
            return []

        # Hydrate node payloads via the per-child handle's get_node so we
        # can apply the same field-AND check the lazy path uses.
        matched: list[tuple[str, dict]] = []
        for node_id in sorted(candidates):
            node = graph_view.get_node(node_id)
            if node is None:
                continue
            if _match_token_groups(token_groups, node_id, node):
                matched.append((node_id, node))
        if not matched:
            return []

        bm25_scores = self._bm25_scores(child_name, matched, token_groups)

        def _key(item: tuple[str, dict]) -> tuple[int, int, float, int, int, str]:
            node_id, node = item
            node_with_id = {"id": node_id, **node}
            return (
                resolution_penalty(node_with_id),
                exact_symbol_match_rank(node_with_id, token_groups),
                -bm25_scores.get(node_id, 0.0),
                authority_score(node_with_id),
                confidence_score(node_with_id),
                node_id,
            )

        scored = sorted(matched, key=_key)
        return [{"id": nid, **node} for nid, node in scored[:limit]]

    # -- internals -------------------------------------------------------

    def _candidates_strict_and(
        self,
        token_groups: list[list[str]],
        child_name: str,
    ) -> set[str]:
        """Per-group union, AND across groups, restricted to one child."""
        per_token = self._per_child_token_to_nodes.get(child_name, {})
        if not per_token:
            return set()
        result: set[str] | None = None
        for group in token_groups:
            group_hits: set[str] = set()
            for tok in group:
                if not tok:
                    continue
                for indexed_token, ids in per_token.items():
                    if tok in indexed_token:
                        group_hits.update(ids)
            if result is None:
                result = group_hits
            else:
                result &= group_hits
            if not result:
                return set()
        return result if result is not None else set()

    def _bm25_scores(
        self,
        child_name: str,
        matched: list[tuple[str, dict]],
        token_groups: list[list[str]],
    ) -> dict[str, float]:
        """Per-node BM25 scores from in-memory dicts (reuses K1/B from sqlite_query)."""
        if self.avg_length <= 0 or self.total_docs <= 0:
            return {nid: 0.0 for nid, _ in matched}

        per_node_freq = self._per_child_node_freq.get(child_name, {})
        df_cache: dict[str, int] = {}
        scores: dict[str, float] = {}
        for node_id, _node in matched:
            node_freq = per_node_freq.get(node_id, {})
            if not node_freq:
                scores[node_id] = 0.0
                continue
            length = self.doc_lengths.get(
                (child_name, node_id), sum(node_freq.values()),
            )
            scores[node_id] = self._score_one(
                node_freq=node_freq,
                length=length,
                token_groups=token_groups,
                df_cache=df_cache,
            )
        return scores

    def _score_one(
        self,
        *,
        node_freq: dict[str, int],
        length: int,
        token_groups: list[list[str]],
        df_cache: dict[str, int],
    ) -> float:
        if not node_freq:
            return 0.0
        frequencies = Counter(node_freq)
        score = 0.0
        for group in token_groups:
            score += max(
                self._term_score(
                    term=term,
                    frequencies=frequencies,
                    length=length,
                    df_cache=df_cache,
                )
                for term in group
            )
        return score

    def _term_score(
        self,
        *,
        term: str,
        frequencies: Counter[str],
        length: int,
        df_cache: dict[str, int],
    ) -> float:
        tf = sum(c for tok, c in frequencies.items() if term in tok)
        if tf <= 0 or self.avg_length <= 0:
            return 0.0
        idf = self._idf(term, df_cache)
        denom = tf + _BM25_K1 * (1 - _BM25_B + _BM25_B * length / self.avg_length)
        return idf * (tf * (_BM25_K1 + 1)) / denom

    def _idf(self, term: str, df_cache: dict[str, int]) -> float:
        cached = df_cache.get(term)
        if cached is None:
            df = sum(
                1
                for indexed_token in self.inverted
                if term in indexed_token
            )
            df_cache[term] = df
        else:
            df = cached
        if df <= 0 or self.total_docs <= 0:
            return 0.0
        return math.log(1 + (self.total_docs - df + 0.5) / (df + 0.5))


def build_eager_index_for(federation: object) -> EagerFederationIndex:
    """Aggregate every fresh-sidecar child of *federation* (ADR 0063).

    Pulled out of ``FederatedGraph`` so the federation module stays
    under the line-count cap. Iterates ``federation._children`` in
    sorted order, calling ``federation._load_child(name)`` per name and
    keeping only :class:`SqliteBackedGraph` results.
    """
    sqlite_children = [
        (name, handle)
        for name in sorted(federation._children)  # noqa: SLF001
        for handle in [federation._load_child(name)]  # noqa: SLF001
        if isinstance(handle, SqliteBackedGraph)
    ]
    return EagerFederationIndex.build(sqlite_children)


def _aggregate_one_child(
    name: str,
    conn: sqlite3.Connection,
    inverted: dict[str, list[tuple[str, str, int]]],
    doc_lengths: dict[tuple[str, str], int],
) -> None:
    """Walk one child's index tables and append rows into the aggregate dicts.

    Pulled out as a separate helper so :meth:`EagerFederationIndex.build`
    has one place to wrap per-child sqlite errors and skip a misbehaving
    sidecar without aborting the whole aggregation.
    """
    for token, node_id, freq in conn.execute(
        "SELECT token, node_id, frequency FROM token_index"
    ):
        inverted.setdefault(token, []).append((name, node_id, int(freq)))
    for node_id, length in conn.execute(
        "SELECT node_id, length FROM token_doc_lengths"
    ):
        doc_lengths[(name, node_id)] = int(length)


def _match_token_groups(
    token_groups: list[list[str]], nid: str, node: dict,
) -> int:
    """Field-AND check identical to :func:`weld._sqlite_query._match_token_groups`.

    Duplicated locally to keep this module independent of the
    sqlite_query module's internal helper. The contract is small and
    stable; the lazy and eager paths must agree on field semantics for
    the match-set parity test to hold.
    """
    nid_l = nid.lower()
    label_l = node.get("label", "").lower()
    props = node.get("props") or {}
    file_l = (props.get("file") or "").lower()
    qualname_l = str(props.get("qualname") or "").lower()
    exports_l = [e.lower() for e in props.get("exports", []) if isinstance(e, str)]
    constants_l = [c.lower() for c in props.get("constants", []) if isinstance(c, str)]
    desc_l = (props.get("description") or "").lower()
    hits = 0
    for group in token_groups:
        if any(
            t in nid_l or t in label_l or t in file_l or t in desc_l
            or t in qualname_l
            or any(t in e for e in exports_l)
            or any(t in c for c in constants_l)
            for t in group
        ):
            hits += 1
        else:
            return 0
    return hits
