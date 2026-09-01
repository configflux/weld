"""The query-quality eval corpus: every reported dogfood-gap query, pinned.

Data for ``weld_query_corpus_gate_test``. ADR 0113 is the decision; bd 2gvr is
the bundle. Each entry is one query a human actually ran, with the answer they
actually needed, taken from the bd issue that reported it. The population those
queries run against lives in :mod:`weld.tests._query_corpus_nodes`.

Why a fixture graph and not the live one
----------------------------------------
This repo is Mode A (ADR 0076): ``.weld/graph.json`` is not committed, so a gate
that queried the live graph could not run hermetically and would answer
differently every hour. But hermeticity is only the second reason.

The first is that the fixture carries the adversarial concept nodes on purpose,
so filing "query X returns noise" cannot cosmetically answer query X (bd 9ucf).
A live-graph gate would lose exactly that the moment somebody closed the issues.
See the fixture module for the full argument.

Extending this corpus
---------------------
A new dogfood-gap fix adds an entry here, and adds the gap issue's own concept
node to ``CONCEPT_NODES`` next door. Filing the issue is what makes the entry
adversarial, so the mechanism that used to hide the bug now hardens the test
against it.

Which impls answer these queries
--------------------------------
Two gates read this corpus, both under ``bazel test //...``.
``weld_query_corpus_gate_test`` asks impl #1 (the in-memory JSON ``Graph``);
``weld_query_backend_parity_test`` asks all three, adding impl #2 (the sqlite
sidecar) and impl #3 (eager federation). A corpus that only ever asks one
reader cannot notice when the other two stop agreeing with it -- which they
had, until bd cgj3.
"""

from __future__ import annotations

from weld.tests._query_corpus_nodes import (
    CONCEPT_NODES,
    NOISE_NODES,
    SUBJECT_NODES,
    fixture_nodes,
)
from weld.tests._query_corpus_traversal import (  # noqa: F401 -- re-export
    TRAVERSAL_CORPUS,
)

#: One entry per reported query.
#:
#: * ``must_contain`` -- node ids that must appear in the result set at all.
#:   The retrieval half: these are the nodes the reporter needed.
#: * ``must_not_rank_first`` -- id prefixes that must not hold rank 1. The
#:   ranking half, and the standing guard against the bd 9ucf self-heal.
#: * ``must_lead`` -- optional: the id that must be rank 1 outright, used only
#:   where the reported answer is unambiguous.
CORPUS: tuple[dict, ...] = (
    {
        "bd": "pxjc",
        "query": "tree-sitter availability gate",
        "must_contain": ["file:weld/strategies/tree_sitter"],
        "must_not_rank_first": ["concept:"],
        "why": (
            "Reported as a ranking failure; measurement found two causes. The "
            "query matched nothing but its own issue's concept node (ADR 0113 "
            "candidacy), and 'tree-sitter' is not a substring of 'tree_sitter' "
            "so no tree-sitter node was ever a candidate (separator variants)."
        ),
    },
    {
        "bd": "c64p",
        "query": "graph storage path resolution project root",
        "must_contain": ["symbol:py:weld.graph:Graph"],
        "must_not_rank_first": ["concept:"],
        "why": (
            "Returned exactly one match -- its own bug report -- so the graph "
            "path resolver was never a candidate. The reporter needed the "
            "Graph class that constructs the path."
        ),
    },
    {
        "bd": "9ucf",
        "query": "where is graph.json written",
        "must_contain": ["file:weld/serializer"],
        "must_not_rank_first": ["concept:"],
        "must_lead": "file:weld/serializer",
        "why": (
            "The issue that named the self-heal: its own concept node must "
            "never lead the query its title quotes, and the write funnel it "
            "asked for must come back alongside it. The funnel is reachable "
            "here because the serializer's own opening docstring line names "
            "graph.json and discovery now records it (bd ph1g); before that "
            "this entry needed a hand-written stand-in to have anything to "
            "retrieve. must_lead is the remaining ranking half (bd dyam): "
            "retrieval alone let a generic verb ('written', matched by name "
            "on unrelated lint symbols) outrank the rare subject token "
            "('graph.json', carried only in the serializer's summary) -- "
            "fixed by counting a summary hit as identity evidence, the same "
            "class of evidence description already was."
        ),
    },
    {
        "bd": "ph1g",
        "query": "graph.json",
        "must_contain": ["file:weld/serializer"],
        "must_not_rank_first": ["concept:"],
        "why": (
            "The lexical half of bd 9ucf, and the entry that most needs the "
            "adversarial fixture: measured against the live graph, the ONLY "
            "node carrying the substring 'graph.json' anywhere was the concept "
            "node minted from this issue's own title. The token could reach "
            "nothing else, so the bare filename returned the bug report about "
            "the bare filename returning nothing. The fix records the opening "
            "line of a module's docstring on props.summary, which is where the "
            "serializer had said 'Canonical serializer for graph.json' all "
            "along. Both concept nodes here carry the token too, so the entry "
            "keeps proving retrieval reached the code rather than the backlog."
        ),
    },
    {
        "bd": "p6ke",
        "query": "emit canonical text",
        "must_contain": ["symbol:py:weld.serializer:dumps_graph"],
        "must_not_rank_first": ["concept:"],
        "must_lead": "symbol:py:weld.serializer:dumps_graph",
        "why": (
            "ADR 0114 gave file: nodes props.summary from the module "
            "docstring; symbol nodes -- including "
            "symbol:py:weld.serializer:dumps_graph, the exact node bd "
            "9ucf/ph1g's own report named -- got none, so a name stated "
            "only in a function's own docstring, nowhere in its "
            "signature, still matched nothing. 'emit', 'canonical' and "
            "'text' appear only in dumps_graph's opening docstring line "
            "('Emit the canonical JSON text for ``graph``.') -- not in "
            "its id, label, qualname or file -- so this is reachable "
            "only through the same channel bd ph1g wired for the file "
            "one level up. must_lead is unambiguous: 'text' rules out "
            "file:weld/serializer (whose own summary carries 'canonical' "
            "but not 'text'), leaving dumps_graph the sole strict-AND "
            "match."
        ),
    },
    {
        "bd": "atcb",
        "query": "broken_reference diagnostics",
        "must_contain": ["file:weld/agent_graph_metadata_diagnostics"],
        "must_not_rank_first": ["concept:", "symbol:py:tools."],
        "why": (
            "Returned exactly one match -- a test *about* the diagnostic -- so "
            "strict-AND 'succeeded' and the module that emits it was never a "
            "candidate. The same candidacy failure as bd pxjc/c64p with a test "
            "in the concept's place, which is why ADR 0113 derives 'not "
            "evidence' from the demotion predicates rather than naming concepts."
        ),
    },
    {
        "bd": "to8x",
        "query": "graph json dump serializer",
        "must_contain": ["file:weld/serializer"],
        "must_not_rank_first": ["concept:", "symbol:py:tools."],
        "why": (
            "bd to8x's collision -- lint tests ABOUT json.dumps outranking the "
            "graph.json write funnel -- reduced to the token set that keeps "
            "strict-AND alive. The reduction is the point: to8x's verbatim "
            "six-token query relaxes to the OR fallback on a fixture this "
            "size, and or_fallback_sort_key has carried test_noise_demotion in "
            "all three impls since to8x landed. Only the STRICT-AND key was "
            "fixed, in impl #1 alone, so the verbatim query could never have "
            "caught the drift. Before bd cgj3 this entry was led by the lint "
            "test on the sqlite and federation paths and by the serializer on "
            "the JSON path: one query, one graph, three answers."
        ),
    },
    {
        "bd": "ziv1",
        "query": "repo boundary caching ADR decision",
        "must_contain": ["file:weld/repo_boundary"],
        "must_not_rank_first": ["concept:"],
        "why": (
            "The code half of the report, which holds today and is unchanged "
            "by the fix below -- this entry stays a query-retrieval pin, not "
            "an edge-existence check. The doc half -- a documents edge from "
            "the ADRs that name this module -- is fixed (ADR 0128): "
            "docs/adrs/*.md is now discovered at all (it had no source entry "
            "before this), and the markdown strategy's new doc -> code "
            "citation pass mints file:weld/repo_boundary inbound edges from "
            "doc:docs/adrs/0020-... and doc:docs/adrs/0027-..., both "
            "directions queryable via wd context. Pinned as its own gate, "
            "not folded into this query-corpus mechanism, because the fix is "
            "an edge-existence property (wd context inbound/outbound) rather "
            "than a wd query ranking property: see "
            "weld_adr_governs_module_edges_repo_test.py, which also pins "
            "that ADR 0012 -- named in the original report but never "
            "actually citing the module in its own body -- correctly gets no "
            "edge, rather than loosening the match rule to force one."
        ),
    },
    {
        "bd": "2oa4",
        "query": "agent_direct_payload",
        "must_contain": [
            "symbol:py:weld._enrich_agent_direct:agent_direct_payload",
        ],
        "must_not_rank_first": ["concept:"],
        "why": (
            "Reported as empty when discovery was briefly blind to two "
            "committed modules -- a transient the reporter suspected and "
            "correctly flagged. Pinned so the blindness cannot return "
            "unnoticed, which a one-shot re-run would never have caught."
        ),
    },
    {
        "bd": "lid2",
        "query": "mcp_server Tool build_tools",
        "must_contain": ["symbol:py:weld.mcp_server:Tool"],
        "must_not_rank_first": ["concept:"],
        "why": (
            "Retrieval for the class holds. The reported defect -- the "
            "missing symbol->symbol reference edge from build_tools -- is "
            "fixed by ADR 0127's `references` edge type (a bare-name VALUE "
            "reference that resolves to a same-module top-level symbol); "
            "`wd context symbol:py:weld.mcp_server:Tool` now shows "
            "build_tools inbound. Pinned so retrieval for the class does "
            "not regress alongside the edge fix."
        ),
    },
    {
        "bd": "2xoj",
        "query": "discover.yaml",
        "must_contain": [
            "symbol:py:weld._workspace_bootstrap:_child_has_discover_yaml",
        ],
        "must_not_rank_first": ["concept:"],
        "why": (
            "The index's own separator alphabet is six characters wide "
            "(weld.query_index.SEPARATOR_CHARS: '/:.·-_') but the query side "
            "re-spelled only '-'/'_' (bd pxjc). _child_has_discover_yaml has "
            "no docstring at all, and its name spells the underscore form "
            "only, so a dotted query token for the config file's real "
            "on-disk name ('discover.yaml') had no channel to reach it at "
            "all before this fix: not a ranking failure, a candidacy "
            "failure, the same shape as bd pxjc one separator further out. "
            "A single-token query is what isolates the mechanism cleanly -- "
            "strict-AND and the OR fallback are the same thing for one "
            "group, so there is no second retrieval path that could rescue "
            "the match by accident the way a multi-token AND query's "
            "empty-intersection fallback would. Deliberately unrelated "
            "vocabulary to the graph.json cluster the ph1g/9ucf entries "
            "share: reusing 'graph.json' here would have added a fourth "
            "competitor to that shared-fixture pool and perturbed bd 9ucf's "
            "pinned must_lead through BM25 arithmetic unrelated to the "
            "separator fix (measured while building this entry -- reverted "
            "once found)."
        ),
    },
    {
        "bd": "ikof",
        "query": "incremental discovery equivalence full",
        "must_contain": ["file:weld/tests/incremental_refresh_equivalence_test"],
        "must_not_rank_first": ["concept:"],
        "must_lead": "file:weld/tests/incremental_refresh_equivalence_test",
        "why": (
            "Querying by the invariant a test proves ('incremental discovery "
            "equals full discovery') surfaced only production modules -- none "
            "of the six incremental_*_equivalence_test.py files appeared at "
            "all, despite their own docstrings stating exactly this invariant. "
            "Two compounding causes: test_peer never read a test file's "
            "docstring into props.summary at all (every other discovery "
            "strategy has carried this channel since ADR 0114/0118/0124), so "
            "the evidence did not exist to retrieve; and even with it added, "
            "test_noise_demotion sorted every test node behind every "
            "non-test node regardless of match strength, which the live "
            "graph's 147+ generically-'incremental' non-test matches turned "
            "into a hard floor under the default result window. Fixed with "
            "both halves: test_peer now gives Python test files the same "
            "summary channel every other node kind has, and "
            "test_noise_demotion gained a narrow exemption -- high total "
            "coverage (ADR 0075's own max(2, N-1) bar) AND the node's own "
            "summary genuinely contributing a group beyond its filename/"
            "qualname -- which bd to8x's and bd atcb's adversarial noise "
            "nodes fail on the second half (their coverage comes entirely "
            "from a verbose name, with no summary at all), so neither of "
            "those entries' orders moved. must_lead is unambiguous here: "
            "file:weld/_incremental_purge is real production competition "
            "(2 of 4 groups) but the test file's own summary carries a "
            "third group ('full') no other fixture node in this pair states."
        ),
    },
    {
        "bd": "mnhl",
        "query": "terminal-write-boundary",
        "must_contain": ["symbol:py:weld.doctor:main"],
        "must_not_rank_first": ["concept:"],
        "why": (
            "The original query ('render node id stderr output') surfaced "
            "renderers by name similarity but missed 8 of ~11 real terminal "
            "write-boundary sites, including weld/doctor.py, because no "
            "structural fact said 'this symbol writes to the terminal'. "
            "ADR 0129 derives one from the calls graph already built: a "
            "symbol that calls weld._safe_text.sanitize_terminal_text/_line "
            "(the ADR 0025/rn0x mandated write-boundary chokepoint) is "
            "tagged output_sink='terminal' plus this exact keyword, via the "
            "ADR-0105 channel. The compound, hyphenated query is deliberate: "
            "queried on the live graph it returns exactly the 45 marked "
            "symbols and nothing else (verified at fix time), while the "
            "3-separate-word phrasing dilutes past the default result "
            "window before reaching every site. bd mnhl's own concept node "
            "contains 'write' and 'terminal' as separate words but never "
            "'boundary', so it cannot enter strict-AND candidacy for this "
            "query at all -- the must_not_rank_first assertion holds "
            "structurally, not by chance."
        ),
    },
)

__all__ = [
    "CONCEPT_NODES",
    "CORPUS",
    "NOISE_NODES",
    "SUBJECT_NODES",
    "TRAVERSAL_CORPUS",
    "fixture_nodes",
]
