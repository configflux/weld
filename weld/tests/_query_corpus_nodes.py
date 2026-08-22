"""The fixture graph every query-corpus entry is answered against.

Split out of :mod:`weld.tests.query_corpus` when that module reached the
line-count cap. The seam is deliberate rather than arbitrary: the queries and
their contracts are the corpus, and this is the *population* they run over.
Sibling-fixture module + both consumers listing it in ``srcs`` is the pattern
``_sqlite_query_test_fixtures.py`` already uses.

Why this population and not a simpler one
-----------------------------------------
The fixture carries the adversarial ``concept:`` nodes on purpose.
``concept_from_bd`` mints one node per gap issue, labelled after the issue
title -- and a gap title quotes the query it reports. So filing "query X returns
noise" creates a node that matches query X and can cosmetically answer it. Two
of the reported queries once returned *nothing but* their own bug report, which
is why ``must_not_rank_first: ["concept:"]`` is a live assertion here rather
than a note.

Node ids are real: each was verified present in this repo's graph when its entry
was added, so drift surfaces as a stale id rather than a silently passing gate.
"""

from __future__ import annotations

from weld.tests._query_corpus_helpers import _concept, _file, _symbol
from weld.tests._query_corpus_noise import NOISE_NODES

#: The adversarial half of the fixture: one concept node per gap issue, minted
#: from that issue's real title. These are what made the reported queries
#: "self-heal" (bd 9ucf). Every corpus entry competes against them.
CONCEPT_NODES: dict[str, dict] = {
    "concept:query-for-the-tree-sitter-availability-gate-ranked-an-unrelated-test-class-first": _concept(  # noqa: E501
        "query-for-the-tree-sitter-availability-gate-ranked-an-unrelated-test-class-first",
        "weld dogfood gap: query for the tree-sitter availability gate ranked "
        "an unrelated test class first",
        "pxjc",
    ),
    "concept:weld-brief-cannot-answer-graph-storage-path-resolution-project-root": _concept(  # noqa: E501
        "weld-brief-cannot-answer-graph-storage-path-resolution-project-root",
        "weld dogfood gap: weld_brief cannot answer 'graph storage / path "
        "resolution / project root'",
        "c64p",
    ),
    # bd to8x's node, and the adversary for BOTH the 9ucf and to8x entries: its
    # title quotes the query the 9ucf entry pins ("where is graph.json written")
    # and names the collision the to8x entry pins ("lint-test noise, not the
    # serializer"). bd 9ucf's own title -- which says an issue-derived concept
    # node outranks code for the very query its issue quotes -- is deliberately
    # NOT minted as a second node: it quotes no query, so it would add fixture
    # population (and shift BM25 for every entry) without adding an adversary.
    "concept:where-is-graph-json-written-returns-lint-test-noise-not-the-serializer": _concept(  # noqa: E501
        "where-is-graph-json-written-returns-lint-test-noise-not-the-serializer",
        "weld dogfood gap: 'where is graph.json written' returns lint-test "
        "noise, not the serializer",
        "to8x",
    ),
    # bd ph1g's node, and the sharpest adversary in the fixture: it is the one
    # concept whose title carries the *exact literal token* its entry queries.
    # Before the summary channel existed that made it, briefly, the only node in
    # the whole graph that could answer "graph.json" -- the self-heal in its
    # purest form, where filing the report is what made the report look wrong.
    "concept:no-node-carries-the-literal-token-graph-json-so-the-write-funnel-is-unreachable": _concept(  # noqa: E501
        "no-node-carries-the-literal-token-graph-json-so-the-write-funnel-is-unreachable",
        "weld dogfood gap: no node carries the literal token 'graph.json', so "
        "the write funnel is unreachable by that name",
        "ph1g",
    ),
    "concept:broken-reference-checker-module-not-surfaced-by-weld-query": _concept(
        "broken-reference-checker-module-not-surfaced-by-weld-query",
        "weld dogfood gap: broken_reference diagnostics checker module not "
        "surfaced by weld_query",
        "atcb",
    ),
    "concept:adrs-that-govern-a-code-module-are-not-reachable-from-that-module": _concept(  # noqa: E501
        "adrs-that-govern-a-code-module-are-not-reachable-from-that-module",
        "weld dogfood gap: repo boundary caching ADR decision -- ADRs that "
        "govern a code module are not reachable from that module",
        "ziv1",
    ),
    "concept:graph-reports-fresh-while-blind-to-two-committed-enrichment-modules": _concept(  # noqa: E501
        "graph-reports-fresh-while-blind-to-two-committed-enrichment-modules",
        "weld dogfood gap: enrich agent direct -- graph reports fresh while "
        "blind to two committed enrichment modules",
        "2oa4",
    ),
    # bd mnhl -- carries "write"/"terminal" but never "boundary" (see
    # query_corpus.py's entry for why that matters).
    "concept:no-way-to-enumerate-the-code-paths-that-write-graph-derived-text-to-a-terminal": _concept(  # noqa: E501
        "no-way-to-enumerate-the-code-paths-that-write-graph-derived-text-to-a-terminal",
        "weld dogfood gap: no way to enumerate the code paths that write "
        "graph-derived text to a terminal",
        "mnhl",
    ),
}

#: The subject nodes each reported query was actually looking for, plus the
#: test/tooling material that outranked them. Real ids from this repo's graph.
SUBJECT_NODES: dict[str, dict] = {
    # bd pxjc -- the tree-sitter availability gate.
    "file:weld/strategies/tree_sitter": _file(
        "tree_sitter", "weld/strategies/tree_sitter.py",
        constants=["TREE_SITTER_AVAILABLE"],
    ),
    "symbol:py:weld.bench.adapters.weld:_is_tree_sitter_available": _symbol(
        "_is_tree_sitter_available", "weld.bench.adapters.weld",
        "weld/bench/adapters/weld.py",
    ),
    # bd c64p -- where the graph lives / how its path is resolved.
    "symbol:py:weld.graph:Graph": _symbol(
        "Graph", "weld.graph", "weld/graph.py", kind="class",
    ),
    "file:weld/graph": _file("graph", "weld/graph.py"),
    # bd atcb -- the module that emits the broken_reference diagnostic.
    "file:weld/agent_graph_metadata_diagnostics": _file(
        "agent_graph_metadata_diagnostics",
        "weld/agent_graph_metadata_diagnostics.py",
        constants=["broken_reference"],
    ),
    "file:weld/agent_graph_metadata": _file(
        "agent_graph_metadata", "weld/agent_graph_metadata.py",
    ),
    # bd 9ucf / bd ph1g -- the graph.json write funnel. ``summary`` is the real
    # opening line of weld/serializer.py, verbatim, which is what makes this
    # node carry the token at all. It used to be a hand-written ``description``
    # standing in for a field the live graph had no way to populate: 9ucf's
    # ranking contract needed a node carrying "graph.json", and no node did.
    # bd ph1g closed that by recording the sentence the module already had, so
    # the fixture can now say what discovery says instead of what it wished.
    # ``exports``/``constants`` (bd ght0) are the module's real ones too --
    # BM25 (weld.bm25.BM25Corpus, via weld.query_index.node_tokens) indexes
    # every bag field, not just the identity ones subject_identity_specificity
    # reads, so a file node's REAL document length includes them. Leaving them
    # off made this node artificially short relative to the live one, which is
    # what let it out-BM25 the bd ght0 competitor symbols in the fixture even
    # BEFORE that bug's fix -- the opposite of what the live graph measured.
    "file:weld/serializer": _file(
        "serializer", "weld/serializer.py",
        summary="Canonical serializer for ``graph.json``.",
        exports=["canonical_graph", "dumps_graph", "dumps_graph_canonical"],
        constants=["_ENTITY_SETTINGS", "_HEADER_SETTINGS"],
    ),
    # bd p6ke -- the exact symbol bd 9ucf/ph1g's own report named
    # ("symbol:py:weld.serializer:dumps_graph") and that ADR 0114 did not
    # reach: it gave the FILE node a summary; the symbol one level down got
    # none. ``summary`` below is not a stand-in -- it is the real opening
    # line of ``dumps_graph``'s own docstring, verbatim, the same way
    # ``file:weld/serializer`` above carries the module's.
    "symbol:py:weld.serializer:dumps_graph": _symbol(
        "dumps_graph", "weld.serializer", "weld/serializer.py",
        summary="Emit the canonical JSON text for ``graph``.",
    ),
    # bd ziv1 -- the repo-boundary module the ADRs govern.
    "file:weld/repo_boundary": _file("repo_boundary", "weld/repo_boundary.py"),
    "symbol:py:weld.repo_boundary:path_within_repo_boundary": _symbol(
        "path_within_repo_boundary", "weld.repo_boundary",
        "weld/repo_boundary.py",
    ),
    # bd 2oa4 -- the agent-direct enrichment modules discovery went blind to.
    "symbol:py:weld._enrich_agent_direct:agent_direct_payload": _symbol(
        "agent_direct_payload", "weld._enrich_agent_direct",
        "weld/_enrich_agent_direct.py",
    ),
    "file:weld/_enrich_agent_direct": _file(
        "_enrich_agent_direct", "weld/_enrich_agent_direct.py",
    ),
    # bd lid2 -- the class whose same-module dependents are unreachable.
    "symbol:py:weld.mcp_server:Tool": _symbol(
        "Tool", "weld.mcp_server", "weld/mcp_server.py", kind="class",
    ),
    "symbol:py:weld.mcp_server:build_tools": _symbol(
        "build_tools", "weld.mcp_server", "weld/mcp_server.py",
    ),
    # bd 2xoj -- the dot/underscore separator gap, one hop past bd pxjc's
    # hyphen/underscore fix. Real id: this helper has no docstring at all (a
    # one-line body, so ADR 0118's symbol_summary() records nothing), and its
    # own name spells the underscore form only, so a dotted query token
    # ("discover.yaml", the config file's real on-disk spelling) had no
    # channel to reach it at all before this fix -- reachable ONLY through
    # the widened separator-variant OR-group. Deliberately unrelated
    # vocabulary to the graph.json cluster above (bd 9ucf/ph1g/p6ke): those
    # entries' subjects already compete on "graph"/"json"/"path" tokens, and
    # adding a fourth "graph_json"-shaped node to that shared pool would
    # perturb bd 9ucf's pinned must_lead through BM25 arithmetic having
    # nothing to do with the separator fix under test here. "discover"/
    # "yaml"/"workspace" share no token with any other corpus query, so this
    # entry cannot become a candidate for any of them.
    "symbol:py:weld._workspace_bootstrap:_child_has_discover_yaml": _symbol(
        "_child_has_discover_yaml", "weld._workspace_bootstrap",
        "weld/_workspace_bootstrap.py",
    ),
    # bd ikof -- querying by test-invariant intent surfaced only production
    # modules, never the test that actually proves the invariant. Both
    # summaries are the real opening docstring lines, verbatim, exactly as
    # bd ph1g's file:weld/serializer entry above does. test_peer never gave
    # a test file this channel at all before this fix -- these ids and
    # summaries were verified against this repo's live graph at fix time.
    # "incremental"/"discovery"/"equivalence"/"full" share no token with any
    # other corpus query (checked directly), so this pair cannot become a
    # candidate for any of them.
    "file:weld/tests/incremental_refresh_equivalence_test": _file(
        "incremental_refresh_equivalence_test",
        "weld/tests/incremental_refresh_equivalence_test.py",
        kind="test",
        roles=["test"],
        source_strategy="test_peer",
        summary=(
            "Incremental refresh is byte-equivalent to a full discover "
            "(bd 85tb.2)."
        ),
    ),
    # The weaker real competitor: 2 of 4 groups (incremental, discovery) via
    # its own summary, versus the test file's 3 (adding "equivalence" from
    # its filename and "full" from its own summary). Before this fix no
    # amount of coverage let a test file outrank ANY non-test node, so this
    # pair is what proves the exemption is coverage-driven, not a blanket
    # test-first flip: a query without "incremental"/"full" phrasing that
    # only this node answers must still return this node undemoted.
    "file:weld/_incremental_purge": _file(
        "_incremental_purge", "weld/_incremental_purge.py",
        summary="Provenance-aware edge purge for incremental discovery (ADR 0074).",
    ),
    # bd mnhl -- ADR 0129's output-boundary marker; real props, verified
    # live: weld.doctor.main calls sanitize_terminal_text before writing.
    "symbol:py:weld.doctor:main": _symbol(
        "main", "weld.doctor", "weld/doctor.py",
        summary="CLI entry point for ``wd doctor``.",
        keywords=["terminal-write-boundary"],
        output_sink="terminal",
    ),
}

#: The competing noise. Lives in :mod:`weld.tests._query_corpus_noise`
#: (split out for line-count hygiene) and is re-exported here so
#: ``query_corpus.py``'s import site is unaffected by the split.


def fixture_nodes() -> dict[str, dict]:
    """The one shared graph every corpus entry is queried against.

    Shared rather than per-entry on purpose: ten queries over one node
    population is a retrieval corpus, where ten isolated fixtures would be ten
    unit tests that each prove their own premise.
    """
    return {**CONCEPT_NODES, **SUBJECT_NODES, **NOISE_NODES}


__all__ = ["CONCEPT_NODES", "NOISE_NODES", "SUBJECT_NODES", "fixture_nodes"]
