"""The noise population for the query-quality eval corpus fixture.

Split out of :mod:`weld.tests._query_corpus_nodes` for line-count hygiene --
this dict is the largest, most self-contained block there and grows with
every ranking-collision entry, so it is the natural second split (the first
being :mod:`weld.tests._query_corpus_helpers`, which both this module and
``_query_corpus_nodes`` depend on rather than each other). See that module
for what the fixture as a whole is and why it exists.

Reproduced the way the reports describe the collision rather than the way it
would be easiest to assert on. The reported failure is that OR-fallback
"latched onto 'gate' alone and ranked a test class with many similarly-named
methods above every tree-sitter node" (bd pxjc), and that the top hits for bd
c64p were ``tools/tier_check_*`` tests. So these nodes carry the query's
*generic* token and not its subject -- a noise node written to match every
group would be a different bug, and would let the corpus pass by testing a
collision that never happened.
"""

from __future__ import annotations

from weld.tests._query_corpus_helpers import _file, _symbol

NOISE_NODES: dict[str, dict] = {
    "symbol:py:tools.local_gate_scope_test:LocalGateScopeTest.test_classify_path_doc_suffix": _symbol(  # noqa: E501
        "LocalGateScopeTest.test_classify_path_doc_suffix",
        "tools.local_gate_scope_test", "tools/local_gate_scope_test.py",
    ),
    "symbol:py:tools.local_gate_scope_test:LocalGateScopeTest.test_gate_scope_is_stable": _symbol(  # noqa: E501
        "LocalGateScopeTest.test_gate_scope_is_stable",
        "tools.local_gate_scope_test", "tools/local_gate_scope_test.py",
    ),
    "symbol:py:tools.agent_graph_audit_gate_test:FindingSignatureTests.test_signature_uses_diagnostic_for_broken_reference": _symbol(  # noqa: E501
        "FindingSignatureTests.test_signature_uses_diagnostic_for_broken_reference",
        "tools.agent_graph_audit_gate_test",
        "tools/agent_graph_audit_gate_test.py",
    ),
    "symbol:py:tools.tier_check_test:TierCheckTest.test_storage_root_is_project_relative": _symbol(  # noqa: E501
        "TierCheckTest.test_storage_root_is_project_relative",
        "tools.tier_check_test", "tools/tier_check_test.py",
    ),
    "file:tools/doc_node_id_examples": _file(
        "doc_node_id_examples", "tools/doc_node_id_examples.py",
    ),
    # bd to8x's own noise: a lint test ABOUT ``json.dumps`` whose method name
    # states the subject in a sentence (json + serializer + graph + dump) while
    # the funnel it competes with states itself in a word. Nothing but the file
    # path separates the two -- ``python_callgraph`` stamps
    # ``roles: ["implementation"]`` on a symbol defined inside a test module
    # too, which is the whole reason :mod:`weld._test_paths` exists.
    "symbol:py:tools.lint_terminal_safety_test:JsonSerializerBoundaryTest.test_unwrapped_graph_dump_is_flagged": _symbol(  # noqa: E501
        "JsonSerializerBoundaryTest.test_unwrapped_graph_dump_is_flagged",
        "tools.lint_terminal_safety_test",
        "tools/lint_terminal_safety_test.py",
    ),
    # bd dyam's noise: the ranking half of bd 9ucf/ph1g. 'written' is a
    # generic verb that matches these two by NAME alone (discovery lists the
    # same function under both its file-relative and package-qualified module
    # spelling), while 'graph.json' is a rare, specific token the serializer
    # carries only in its own opening docstring line (props.summary). Both
    # ids verified live: ``wd query "where is graph.json written"`` in this
    # repo ranks both of these ahead of file:weld/serializer before the fix.
    "symbol:py:lint_terminal_safety_ast:written_expr": _symbol(
        "written_expr", "lint_terminal_safety_ast",
        "tools/lint_terminal_safety_ast.py",
    ),
    "symbol:py:tools.lint_terminal_safety_ast:written_expr": _symbol(
        "written_expr", "tools.lint_terminal_safety_ast",
        "tools/lint_terminal_safety_ast.py",
    ),
    # bd ght0's noise: the ADR 0119 separator-widening fallout on bd 9ucf/dyam's
    # OWN entry. All eight ids and summaries verified live
    # (``wd query "where is graph.json written"`` ranked every one of these
    # ahead of file:weld/serializer before this fix). Seven are reachable ONLY
    # because bd 2xoj widened separator-variant matching to reach a bare
    # ``graph_json`` id/qualname substring -- none of their own docstrings (if
    # they have one at all) says "graph.json". ``weld.doctor._check_graph_json``
    # is the eighth and the sharper case: its one-line docstring genuinely
    # says "graph.json" (a raw hit, tier 0, same as the serializer), so the
    # separator-variant tier alone cannot demote it -- only
    # subject_identity_specificity can, because its docstring is longer than
    # the serializer's.
    "symbol:py:doc_node_id_examples:find_graph_json": _symbol(
        "find_graph_json", "doc_node_id_examples",
        "tools/doc_node_id_examples.py",
    ),
    "symbol:py:tools.doc_node_id_examples:find_graph_json": _symbol(
        "find_graph_json", "tools.doc_node_id_examples",
        "tools/doc_node_id_examples.py",
        summary="Return the live graph path if present, else ``None``.",
    ),
    "symbol:py:weld.prime:_check_graph_json": _symbol(
        "_check_graph_json", "weld.prime", "weld/prime.py",
    ),
    "symbol:py:weld.doctor:_check_graph_json": _symbol(
        "_check_graph_json", "weld.doctor", "weld/doctor.py",
        summary="Report graph.json presence + schema/nodes/edges split into sections.",
    ),
    "symbol:py:weld._sqlite_reader:SqliteBackedGraph.graph_json_path": _symbol(
        "SqliteBackedGraph.graph_json_path", "weld._sqlite_reader",
        "weld/_sqlite_reader.py",
        qualname="SqliteBackedGraph.graph_json_path", kind="method",
    ),
    "symbol:py:weld._sqlite_reader:sidecar_freshness": _symbol(
        "sidecar_freshness", "weld._sqlite_reader", "weld/_sqlite_reader.py",
        summary="Return ``(fresh, meta)`` for the sidecar paired with *graph_json_path*.",
    ),
    "symbol:py:weld._sqlite_writer:sidecar_path_for": _symbol(
        "sidecar_path_for", "weld._sqlite_writer", "weld/_sqlite_writer.py",
        summary="Return the sidecar path that pairs with *graph_json_path*.",
    ),
    "symbol:py:weld._sqlite_writer:compute_source_json_sha": _symbol(
        "compute_source_json_sha", "weld._sqlite_writer", "weld/_sqlite_writer.py",
        summary="Return the SHA-256 hex digest of *graph_json_bytes*.",
    ),
}

__all__ = ["NOISE_NODES"]
