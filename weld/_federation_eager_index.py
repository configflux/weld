"""Eager federation inverted-index aggregation (ADR 0063).

Default-on cache (ADR 0063 default-on amendment): walks every
fresh-sidecar child's index tables once at federation construction
time and serves :meth:`FederatedGraph.query` from in-memory dicts
instead of per-query sqlite reads. Strict superset of the lazy path:
stale/missing-sidecar children keep the per-query fallback. Match-set
parity with :func:`weld._sqlite_query.query_sqlite_backed` is the
contract; the federation correctness test asserts it.

The flag-resolution policy (``WELD_FEDERATION_EAGER`` / default) lives
in :mod:`weld._federation_eager_flags`; its names are re-exported here
for backward compatibility with existing imports.
"""

from __future__ import annotations

import sqlite3
from typing import Iterable

from weld._coverage_admission import (
    _COVERAGE_MIN_GROUPS,
    coverage_admissions,
    hydrate_union,
    partial_coverage_subject_miss,
    strip_match,
    tag_match,
)
from weld._federation_eager_bm25 import scores_for_matched
from weld._federation_eager_or_fallback import (
    candidates_union,
    or_fallback_child_matches,
)
from weld._federation_eager_flags import (
    EAGER_DEFAULT,
    EAGER_ENV_VAR,
    env_var_disables,
    env_var_truthy,
    resolve_eager_flag,
)
from weld._sqlite_reader import SqliteBackedGraph
from weld.ranking import (
    authority_score,
    confidence_score,
    exact_symbol_match_rank,
    resolution_penalty,
)
from weld.synonyms import expand_token_groups

__all__ = [
    "EAGER_DEFAULT",
    "EAGER_ENV_VAR",
    "EagerFederationIndex",
    "build_eager_index_for",
    "env_var_disables",
    "env_var_truthy",
    "resolve_eager_flag",
]


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
        # inverted: token -> [(child_name, local_node_id, frequency), ...];
        # doc_lengths: (child_name, local_node_id) -> token-length.
        self.inverted = inverted
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

        ``sqlite_children`` is an iterable of ``(name, handle)`` tuples the
        federation supplies. Non-:class:`SqliteBackedGraph` handles are skipped
        silently; the federation serves them via the lazy/JSON fallback.
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
            return self._or_fallback(graph_view, child_name, tokens, limit)

        # Hydrate node payloads via the per-child handle's get_node so we
        # can apply the same field-AND check the lazy path uses.
        matched: list[tuple[str, dict]] = []
        matched_ids: set[str] = set()
        for node_id in sorted(candidates):
            node = graph_view.get_node(node_id)
            if node is None:
                continue
            if _match_token_groups(token_groups, node_id, node):
                # Tag a diffuse full-coverage doc for demotion (N>=3 only;
                # inert below it). tag_match copies, so payloads are untouched.
                matched.append((node_id, tag_match(node_id, node, token_groups)))
                matched_ids.add(node_id)
        if not matched:
            return self._or_fallback(graph_view, child_name, tokens, limit)

        # ADR 0075 part 1 (8rm0.4 parity): admit high-coverage non-doc nodes the
        # strict-AND intersection dropped, from the per-group UNION restricted to
        # this child. Gated to N>=3 so 1-2 token queries skip the union scan.
        if len(token_groups) >= _COVERAGE_MIN_GROUPS:
            union = candidates_union(
                self._per_child_token_to_nodes.get(child_name, {}), token_groups
            )
            matched.extend(
                coverage_admissions(
                    hydrate_union(graph_view.get_node, union, matched_ids),
                    union,
                    token_groups,
                    matched_ids,
                )
            )

        bm25_scores = self._bm25_scores(child_name, matched, token_groups)

        def _key(
            item: tuple[str, dict]
        ) -> tuple[int, int, int, int, float, int, int, str]:
            node_id, node = item
            node_with_id = {"id": node_id, **node}
            return (
                resolution_penalty(node_with_id),
                exact_symbol_match_rank(node_with_id, token_groups),
                # ADR 0075 diffuse-doc demotion: pre-tagged ``_diffuse`` docs
                # (N>=3 only) sort AFTER non-diffuse nodes (0 < 1), below the
                # bounded-coverage code/entity nodes admitted alongside them.
                # Mirrors rank_query_matches; placed ahead of -bm25.
                int(bool(node.get("_diffuse"))),
                # ADR 0075 subject tie-break (8rm0.4 parity with impl #1); see
                # partial_coverage_subject_miss docstring. Ahead of -bm25.
                partial_coverage_subject_miss(node_with_id, token_groups),
                -bm25_scores.get(node_id, 0.0),
                authority_score(node_with_id),
                confidence_score(node_with_id),
                node_id,
            )

        scored = sorted(matched, key=_key)
        # strip_match drops the internal ``_diffuse`` tag but keeps the
        # ``partial_coverage`` consumer signal (mirrors impl #1's envelope).
        return [strip_match(node, nid) for nid, node in scored[:limit]]

    def _or_fallback(
        self,
        graph_view: SqliteBackedGraph,
        child_name: str,
        tokens: list[str],
        limit: int,
    ) -> list[dict]:
        """OR-fallback match list for one child (parity with the lazy path).

        Single-token queries skip the fallback (OR == AND for one group), like
        :func:`weld._sqlite_query._maybe_or_fallback`; multi-token queries relax
        to the per-group UNION via the shared ``or_fallback_child_matches``.
        """
        if len(tokens) <= 1:
            return []
        token_groups = expand_token_groups(tokens)
        union = candidates_union(
            self._per_child_token_to_nodes.get(child_name, {}), token_groups
        )
        return or_fallback_child_matches(
            union=union,
            get_node=graph_view.get_node,
            bm25_scores_for=lambda matched: self._bm25_scores(
                child_name, matched, token_groups
            ),
            token_groups=token_groups,
            limit=limit,
        )

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
        """Per-node BM25 scores from in-memory dicts (reuses K1/B from sqlite_query).

        Delegates the scoring loop to the free
        :func:`weld._federation_eager_bm25.scores_for_matched` (ADR 0063 split)
        while keeping ownership of the ``(child, node)`` doc-length keying.
        """
        per_node_freq = self._per_child_node_freq.get(child_name, {})
        return scores_for_matched(
            matched=matched,
            node_freqs=per_node_freq,
            doc_length_for=lambda nid: self.doc_lengths.get((child_name, nid)),
            token_groups=token_groups,
            avg_length=self.avg_length,
            total_docs=self.total_docs,
            inverted_tokens=self.inverted,
        )


def build_eager_index_for(federation: object) -> EagerFederationIndex:
    """Aggregate every fresh-sidecar child of *federation* (ADR 0063).

    Iterates ``federation._children`` in sorted order, calling
    ``federation._load_child(name)`` per name and keeping only
    :class:`SqliteBackedGraph` results.
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

    Separate helper so :meth:`EagerFederationIndex.build` has one place to wrap
    per-child sqlite errors and skip a misbehaving sidecar.
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
    token_groups: list[list[str]], nid: str, node: dict) -> int:
    """Field-AND check identical to :func:`weld._sqlite_query._match_token_groups`.

    Duplicated locally to keep this module independent of the
    sqlite_query module's internal helper. The contract is small and
    stable; the lazy and eager paths must agree on field semantics for
    the match-set parity test to hold.

    8rm0.4 field-set unification: ``props.headings`` is now part of the
    surface (it previously was not -- the pre-existing drift ADR 0075 noted).
    Omitting it made the eager path silently disagree with the lazy/sqlite
    path on heading-only doc matches (a confirmed parity divergence), so the
    parity contract and ADR 0075's diffuse-doc demotion both require it here.
    """
    nid_l = nid.lower()
    label_l = node.get("label", "").lower()
    props = node.get("props") or {}
    file_l = (props.get("file") or "").lower()
    qualname_l = str(props.get("qualname") or "").lower()
    exports_l = [e.lower() for e in props.get("exports", []) if isinstance(e, str)]
    constants_l = [c.lower() for c in props.get("constants", []) if isinstance(c, str)]
    headings_l = [h.lower() for h in props.get("headings", []) if isinstance(h, str)]
    desc_l = (props.get("description") or "").lower()
    hits = 0
    for group in token_groups:
        if any(
            t in nid_l or t in label_l or t in file_l or t in desc_l
            or t in qualname_l
            or any(t in e for e in exports_l)
            or any(t in c for c in constants_l)
            or any(t in h for h in headings_l)
            for t in group
        ):
            hits += 1
        else:
            return 0
    return hits
