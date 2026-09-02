"""Per-language tree-sitter extraction, inheritance, and origin suites.

The language front-ends: what each grammar extracts, the canonical kind
mapping it must produce, the parse/query caches on the hot path, the
``inherits``/``implements`` emitters, and the origin attribution that follows
a symbol back to its defining language.

Two lanes live here, and the difference is load-bearing:

* **Hermetic** -- grammars pinned in ``requirements_lock.txt`` (ADR 0022
  lineage), so these run sandboxed like everything else. Targets that mock
  ``tree_sitter`` wholesale, or that only exercise regex helpers, also belong
  here even when their subject is C#/Java (bd m674). Being *pinned* in the
  lockfile is not the same as being *reachable*: a target that really parses
  must also declare ``@pypi//tree_sitter`` plus its grammar, or it resolves
  them from whatever the ambient interpreter happens to carry and skips green
  where that is nothing. Those targets are declared individually below.
* **Ambient (`no-sandbox`, `local = True`)** -- the C#/Java integration lane
  from ADR 0069 (bd f5ku/ydyl). Those wheels are deliberately NOT in the
  lockfile; the tests parse via ambient grammars and self-skip when sandboxed.
  This is a documented exclusion, not a flake-hygiene miss. Do not vendor the
  wheels or strip the tags without superseding ADR 0069.

Extracted verbatim from weld/tests/BUILD.bazel (bd hpv7): that move preserved
every name, src, data, dep, tag, and locality, and every label still reads
//weld/tests:<name>. Deps have been corrected since -- see bd c42b on
weld_cpp_type_uses_test -- so read the declarations below, not the move.
"""

load("@rules_python//python:defs.bzl", "py_test")

_QUERY_FILES = ["//weld/languages:query_files"]
_STRATEGIES = ["//weld/strategies", "//weld/strategies:helpers"]
_RUNTIME_STRATEGIES = ["//weld:runtime", "//weld/strategies", "//weld/strategies:helpers"]
_FIXTURES_AND_QUERY_FILES = [":fixture_files", "//weld/languages:query_files"]

# ADR 0064 criterion 1: weld_ts_definitions_test
# pins the canonical kind mapping; weld_ts_call_graph_kind_test pins file/unresolved
# sentinel kinds; weld_ts_parse_cache_test, weld_callgraph_parse_cache_test, and
# weld_ts_query_cache_test pin the three per-discover cache hot paths. bd m674:
# the four cpp/origin targets moved here out of the ADR 0069 ambient lane -- the
# cpp grammar IS pinned and these self-skip nothing under the sandbox (regex
# helpers plus mocked tree_sitter); weld_language_origin_integration_test mocks
# tree_sitter wholesale, so it carries no ambient csharp/java dependency.
# Every member below is grammar-free by construction; bd c42b measured the whole
# list under a blocked user site and only the ADR 0061 cpp real-parse target
# degraded, which is why that one now declares its grammar explicitly.
#
# weld_cpp_system_include_test is grammar-free too -- its live cases call only
# pure-Python STDLIB_INCLUDE_ROOTS probing plus classify_resolved_include, no
# tree_sitter import -- so its membership here is correct on the axis this
# lane checks. But two of its cases self-skip on a DIFFERENT ambient fact: no
# host C++ stdlib root. That is an accepted gap (bd yvtz), not a dep/wiring
# discrepancy of the bd c42b kind -- STDLIB_INCLUDE_ROOTS names real host
# toolchain paths that are deliberately not vendored. The skip itself is
# attributable (see _NO_HOST_STDLIB_ROOT in the test); recorded here too
# because a _HERMETIC_GRAMMAR reader auditing this list for exactly this
# failure mode would not otherwise find it.
_HERMETIC_GRAMMAR = (
    "tree_sitter_strategy_test",
    "tree_sitter_missing_grammar_test",
    "weld_ts_definitions_test",
    "weld_ts_call_graph_kind_test",
    "weld_ts_parse_cache_test",
    "weld_callgraph_parse_cache_test",
    "weld_ts_query_cache_test",
    "weld_cpp_inherits_test",
    "weld_cpp_system_include_test",
    "weld_cpp_symbol_records_test",
    "weld_language_origin_integration_test",
    "weld_typescript_ast_extraction_test",
    "weld_typescript_brace_glob_test",
    "weld_typescript_tsx_variant_test",
    "weld_typescript_origin_test",
    "weld_typescript_origin_integration_test",
    "weld_rust_extraction_test",
    "weld_callgraph_treesitter_test",
)

# ADR 0069 ambient lane. weld_csharp_partial_class_inheritance_test pins the
# partial-class canonical-symbol retarget; weld_csharp_inheritance_resolve_test
# pins external-base FQN normalisation; bd 3kej: weld_java_inherits_test pins
# ADR 0064 criterion 2 extractor/resolver/emitter.
_AMBIENT_GRAMMAR = (
    "weld_csharp_treesitter_test",
    "weld_csharp_inheritance_test",
    "weld_csharp_inheritance_treesitter_test",
    "weld_csharp_inheritance_resolve_test",
    "weld_csharp_partial_class_inheritance_test",
    "weld_java_inherits_test",
    "weld_java_treesitter_test",
    "weld_java_origin_test",
    # bd 5038-cw4f (ADR 0125 follow-up): test_peer's file-level
    # props.summary reader for Java test files, same ambient lane as every
    # other Java real-parse target above (no @pypi//tree_sitter_java
    # target exists -- confirmed via `bazel query`).
    "weld_test_peer_java_file_summary_test",
)

# ADR 0056 Wave 3: these two additionally carry the shared partial-class helper.
_AMBIENT_PARTIAL_CLASS = ("weld_csharp_partial_class_test", "weld_csharp_partial_generics_test")

# ADR 0064 criterion 2 pure-helper suites: they exercise the ``inherits``
# emitters directly against in-memory graphs, so they need no grammar, no
# fixture, and only the strategy package. bd 2jt5.2.5 (TS), bd w8dj (Go).
# bd rifzk: weld_go_inherits_finalise_test is the edge-emission half split
# out of weld_go_inherits_test at the line-count cap (mirrors the production
# _go_inherits_extract.py / _go_inherits.py module boundary) -- same
# grammar-free, fixture-free shape, so it joins this lane rather than a new
# one.
_INHERITS_UNIT = (
    "weld_typescript_inherits_test",
    "weld_rust_inherits_test",
    "weld_go_inherits_test",
    "weld_go_inherits_finalise_test",
)

# ADR 0062: C++ origin attribution over the shared resolver fakes. Not every
# member needs //weld:contract; the extra dep is harmless and lets all three
# share one declaration.
_CPP_RESOLVER_SUITE = (
    "weld_cpp_origin_integration_test",
    "weld_cpp_header_pairing_test",
    "weld_cpp_amalgamation_test",
)

def treesitter_tests():
    [py_test(
        name = _name,
        srcs = [_name + ".py"],
        data = _QUERY_FILES,
        deps = _STRATEGIES,
    ) for _name in _HERMETIC_GRAMMAR]

    [py_test(
        name = _name,
        srcs = [_name + ".py"],
        deps = _RUNTIME_STRATEGIES,
        local = True,
        tags = ["no-sandbox"],
    ) for _name in _AMBIENT_GRAMMAR]

    [py_test(
        name = _name,
        srcs = [_name + ".py", "_csharp_partial_class_lib.py"],
        deps = _RUNTIME_STRATEGIES,
        local = True,
        tags = ["no-sandbox"],
    ) for _name in _AMBIENT_PARTIAL_CLASS]

    [py_test(
        name = _name,
        srcs = [_name + ".py"],
        deps = ["//weld/strategies"],
    ) for _name in _INHERITS_UNIT]

    # The Go end-to-end discovery suite is split from its pure-helper sibling
    # above so each file stays within the line-count cap: it carries the Go
    # grammar query files plus the fixture tree as data and //weld:runtime for
    # ``weld.discover``, and self-skips when grammar or fixture are not
    # reachable in the sandbox.
    py_test(
        name = "weld_go_inherits_discovery_test",
        srcs = ["weld_go_inherits_discovery_test.py"],
        data = _FIXTURES_AND_QUERY_FILES,
        deps = ["//weld:runtime", "//weld/strategies", "//weld/strategies:helpers", "@pypi//tree_sitter", "@pypi//tree_sitter_go"],
    )

    # bd 5038-009x (ADR 0118 follow-up): Go/Rust symbol-level ``props.summary``
    # from each language's own doc-comment convention. Both really parse (the
    # comment-association walk needs a real sibling tree, not a mocked one),
    # so -- unlike the "grammar-free by construction" _HERMETIC_GRAMMAR group
    # above -- both grammars are declared explicitly rather than hoped for
    # from the ambient interpreter. weld_ts_doc_comments_test pins the
    # backward-walk association logic directly; the _integration_test sibling
    # covers the public entry point, the real tree_sitter.extract() wiring,
    # and read-path reachability via weld.query_index (hence //weld:runtime).
    py_test(
        name = "weld_ts_doc_comments_test",
        srcs = ["weld_ts_doc_comments_test.py"],
        deps = ["//weld/strategies", "@pypi//tree_sitter", "@pypi//tree_sitter_go", "@pypi//tree_sitter_rust"],
    )
    py_test(
        name = "weld_ts_doc_comments_integration_test",
        srcs = ["weld_ts_doc_comments_integration_test.py"],
        data = _QUERY_FILES,
        # tree_sitter_typescript too: the deferred-language case really
        # parses a .ts file and asserts its symbol is minted, which only
        # the ambient interpreter provided before (bd uaz2d).
        deps = ["//weld:runtime", "//weld/strategies", "//weld/strategies:helpers", "@pypi//tree_sitter", "@pypi//tree_sitter_go", "@pypi//tree_sitter_rust", "@pypi//tree_sitter_typescript"],
    )

    # bd 5038-cw4f (ADR 0125 follow-up): test_peer's file-level
    # props.summary reader for Go/Rust/TypeScript test files -- a FILE-level
    # leading-comment read, distinct from the SYMBOL-level walk the two
    # targets above cover (see weld/strategies/_ts_file_doc_comments.py's
    # module docstring for why that distinction lets TypeScript ship here
    # while its symbol-level reader stays deferred). Really parses (the
    # comment-to-file association needs a real parse tree), so all three
    # grammars are declared explicitly rather than hoped for from the
    # ambient interpreter -- Go/Rust/TypeScript are all pinned in
    # requirements_lock.txt (confirmed via `bazel query`), unlike Java,
    # whose sibling test lives in the _AMBIENT_GRAMMAR lane above instead.
    py_test(
        name = "weld_test_peer_file_summary_test",
        srcs = ["weld_test_peer_file_summary_test.py"],
        deps = [
            "//weld:runtime",
            "//weld/strategies",
            "//weld/strategies:helpers",
            "@pypi//tree_sitter",
            "@pypi//tree_sitter_go",
            "@pypi//tree_sitter_rust",
            "@pypi//tree_sitter_typescript",
        ],
    )

    # ADR 0142 D4/D5 (bd 5038-lrnx1.5): the TypeScript dialect dispatch and the
    # re-export evidence beside it. Both really parse, and here that is the
    # whole point rather than an implementation detail -- gap G4 was a `.tsx`
    # file read by the plain TypeScript grammar, which is a fact no mocked
    # parser has an opinion about, and it survived every mocked TS test in this
    # tree. So the grammar is declared rather than hoped for, exactly as the
    # ADR 0061 C++ target below does and for the same measured reason (bd c42b).
    [py_test(
        name = _name,
        srcs = [_name + ".py"],
        data = _QUERY_FILES,
        deps = _STRATEGIES + ["@pypi//tree_sitter", "@pypi//tree_sitter_typescript"],
    ) for _name in ("weld_ts_dialect_test", "weld_typescript_reexports_test")]

    # ADR 0142 D6 (bd 5038-lrnx1.6): the JavaScript query file and the
    # definition promotion behind it. Really parses, and for a sharper reason
    # than its TypeScript neighbours above: the `imports` and `commonjs_exports`
    # patterns are the first tree-sitter *text predicates* (`#eq?`) in any weld
    # language file, and whether a binding evaluates them inside
    # `QueryCursor.matches` is a fact about py-tree-sitter that no mock states.
    # Drop the predicate and every one-string call in a file becomes an import
    # specifier, silently.
    py_test(
        name = "weld_javascript_extraction_test",
        srcs = ["weld_javascript_extraction_test.py"],
        data = _QUERY_FILES,
        deps = _STRATEGIES + ["@pypi//tree_sitter", "@pypi//tree_sitter_javascript"],
    )

    # ADR 0061 real-parse guardrail ("no churn in cpp.yaml without a failing-test
    # repro first"). It is the one target in this file that parses C++ with the
    # real grammar, so it names both wheels instead of hoping the interpreter
    # brought them: public CI presents no ambient tree-sitter, and bd c42b
    # measured its three real-parse cases skipping green on every run there.
    # Both versions come from requirements_lock.txt, so every lane parses with
    # the same grammar rather than with whatever pip last resolved.
    py_test(
        name = "weld_cpp_type_uses_test",
        srcs = ["weld_cpp_type_uses_test.py"],
        data = _QUERY_FILES,
        deps = _STRATEGIES + [
            "@pypi//tree_sitter",
            "@pypi//tree_sitter_cpp",
            # The shared skip-or-fail-on-missing-grammar branch policy
            # (bd 9txq); this target still owns its own probe.
            "//weld/tests:tier_check_grammar_gate",
        ],
    )

    # bd lrnx1.3 (ADR 0142 D2): the two halves of TypeScript call binding.
    # The first really parses -- what a call site is written inside, and which
    # import bound its callee, are facts about a parse tree that a mocked node
    # graph cannot state -- so it names both wheels rather than hoping the
    # ambient interpreter carries them, the bd c42b rule. The second is
    # grammar-free by construction (it hands close_graph an in-memory graph),
    # which is the same shape as the _INHERITS_UNIT lane above and why it sits
    # beside its parsing half instead of in a lane of its own.
    py_test(
        name = "weld_ts_call_sites_test",
        srcs = ["weld_ts_call_sites_test.py"],
        data = _QUERY_FILES,
        deps = _STRATEGIES + ["@pypi//tree_sitter", "@pypi//tree_sitter_typescript"],
    )

    py_test(
        name = "weld_graph_closure_ts_calls_test",
        srcs = ["_graph_closure_ts_calls_fixture.py", "weld_graph_closure_ts_calls_test.py"],
        deps = ["//weld:runtime", "//weld/strategies"],
    )

    py_test(
        name = "weld_cpp_treesitter_test",
        srcs = ["weld_cpp_treesitter_test.py"],
        data = _FIXTURES_AND_QUERY_FILES,
        deps = _RUNTIME_STRATEGIES,
    )

    py_test(
        name = "weld_cpp_resolver_test",
        srcs = ["cpp_resolver_fakes.py", "weld_cpp_resolver_test.py"],
        data = _FIXTURES_AND_QUERY_FILES,
        deps = _RUNTIME_STRATEGIES,
    )

    [py_test(
        name = _name,
        srcs = ["cpp_resolver_fakes.py", _name + ".py"],
        data = _FIXTURES_AND_QUERY_FILES,
        deps = ["//weld:contract", "//weld:runtime", "//weld/strategies", "//weld/strategies:helpers"],
    ) for _name in _CPP_RESOLVER_SUITE]
