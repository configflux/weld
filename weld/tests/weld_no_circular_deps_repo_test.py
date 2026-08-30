"""Zero-*new*-violations gate: this repo's own real graph, bd 5038-ojg27.

Same shape and reason as ``weld_cross_source_edge_provenance_repo_test.py``
(bd whnwb) and ``weld_node_edge_contract_repo_test.py`` (bd rhuc): the unit
fixtures in ``weld_arch_lint_cycles_test.py`` and
``weld_arch_lint_cycles_exclusion_test.py`` pin the rule's own branch logic
cheaply and hermetically, but cannot, by construction, prove the rule stays
clean against the *next* doc that cross-references another doc, or the next
recursive helper this repo's real strategies extract -- only a real
discovery of this repo's own tree can. This is that gate.

Unlike whnwb/rhuc, a real full discovery of this repo did not report zero
``no-circular-deps`` violations even after the ojg27 fix: 14 SCCs remained,
every one of them a genuine file-level ``depends_on``/``contains`` cycle
among real code files (weld's core discovery/auto-refresh/federation/MCP
engine chief among them) -- not a rule-scoping artifact. They predate that
fix, were unrelated to it, and breaking up ~190 file-node instances of
cross-module coupling is a real module-boundary redesign, not something a
doc-cross-reference fix should attempt as a side effect. They were filed as
bd 5038-1308j with the full anchor list and root-cause reasoning (this
repo's own convention of a deferred/function-local import to break a *real*
two-way symbol need -- see ``weld/arch_lint_cycles.py``'s own ``# late
import to break cycle``, itself one of the original 14). bd 5038-1308j then
classified all 14 in ADR 0130: 8 recorded as worth breaking with a
follow-up bd issue each, 5 accepted with a one-line reason inlined next to
their entry below. Six of the 8 worth-breaking follow-ups are done and
trimmed from the baseline below outright -- ``weld/_warm_cli`` (parameterized
``_warm_cli.build_parser`` instead of importing its two defaults from
``weld.warm``), ``weld/_contract_validators`` (bd 5038-l24d9: moved
``ValidationError`` + the vocabulary constants out of ``contract.py`` into
the dependency-free ``weld/_contract_types.py`` leaf; the three validator
siblings import from the leaf instead of importing them back from
``contract.py``), ``tools/tier1_corpus`` (bd 5038-bv76d: moved
CorpusEntry/LanguagePins to the dependency-free leaf
``tools/_tier1_corpus_types.py``), ``weld/_mcp_dispatch`` (bd
5038-dokwz: ``dispatch()``/``run_stdio()`` now take the live tool registry
as an explicit ``tools_provider`` parameter instead of importing it back
from ``weld.mcp_server``), ``weld/agent_graph_metadata`` (bd
5038-ujv26: moved ``ParsedAgentGraphAsset``, ``DerivedAgentGraphNode``, and
the four private extraction helpers into the dependency-free
``weld/_agent_graph_asset.py`` leaf), ``weld/bench/bench_cli`` (bd
5038-4le0k: deleted ``runner.py``'s legacy ``main()`` shim entirely and
redirected both real external callers -- ``weld/cli.py``'s ``wd bench``
dispatch and ``weld/bench/__main__.py``'s ``py_binary`` entry point --
straight to ``weld.bench.bench_cli.main``), and
``weld/_graph_closure_invariants`` -- two tasks, not one: bd 5038-mx8sd
moved ``Violation`` into the dependency-free ``weld/_arch_lint_types.py``
leaf (the six rule modules that used to late-import it from
``weld.arch_lint`` now import it from the leaf at real top level),
dissolving its original 8-member SCC completely but exposing a smaller,
previously-co-anchored 2-member one underneath --
``weld/arch_lint`` <-> ``weld/arch_lint_cli`` over
``available_rule_ids``/``lint``, a registry/runner dependency rather than
a shared type; bd 5038-efr7z then broke that residual too, the same
dependency-injection shape (ADR 0130 disposition #7) ``weld/_mcp_dispatch``
used -- ``arch_lint_cli.main()`` now takes the registry as required
parameters instead of importing it back. With bd 5038-zw6w4's workspace
schema leaf landed, 7 of the 8 worth-breaking follow-ups were done, leaving
only ``weld/_file_index_incremental``. bd 5038-kxx79 then broke that last
one too: its own docstring named the ownership question directly
(``file_index.build_file_index`` reaching *up* into the "incremental"
module for the one tokenizer both the full walk and the patch path need)
-- ``tokens_from_content``/``tokens_for_file`` moved *down* into
``weld/file_index.py``, the module that already owned ``build_file_index``
and re-exported the raw per-extension extractors, leaving
``_file_index_incremental.py`` a clean one-directional consumer of
``file_index.py``'s build/save/load surface. With all 8 worth-breaking
follow-ups done, 5 remained -- the 5 ACCEPT entries; the WORTH-BREAKING
inventory from bd 5038-1308j is complete.

bd 5038-uuxaz.6-repair then fixed a real semantics
bug: the no-circular-deps walk counted a function-scoped/lazy import (this
repo's own sanctioned cycle-breaking idiom, ADR 0130) with the same weight
as a top-level import, so ``python_module``/``graph_closure`` now mark such
a ``depends_on`` edge ``deferred`` and the rule excludes it. Two anchors
that were entirely lazy-import artifacts (``file:weld/_auto_refresh``,
``file:weld/bench/_public_runner``) genuinely dissolved, and a third
(``file:weld/strategies/_python_callgraph_visitor``) separated cleanly from
a real, unrelated 5-member cycle it had been co-anchoring via one lazy
bridge edge -- that cycle re-anchors below as
``file:weld/strategies/_python_decorates``. 3 ACCEPT entries remain.

So this gate ratchets rather than asserting a bare ``[] == violations``:
any violation whose anchor is NOT in ``_KNOWN_PRE_EXISTING_CYCLE_ANCHORS``
fails it immediately (a regression: a new doc cross-reference cycle, a new
recursive-helper false positive were the exclusion ever narrowed, or a
genuinely new file-level cycle). ``test_baseline_has_no_stale_entries``
closes the other direction: the moment one of the 5 anchors stops
appearing (its cycle actually gets broken), that test starts failing until
the entry is trimmed from the baseline below -- so the baseline cannot
quietly rot into a list of already-fixed issues nobody notices.

Scope note: the ratchet is keyed on the SCC's anchor id (its lowest-sorted
member), matching what `wd lint`'s own violation reporting has always
keyed on -- one violation per component. It catches a brand-new cluster
(a new anchor appears) or a brand-new false-positive category (any doc:/
symbol:-only cluster reappearing) immediately. It does NOT separately flag
an already-baselined cluster quietly growing more members while its
anchor stays put -- pinning exact membership per anchor would make this
gate break on every unrelated file added to weld's core engine, which is
not this rule's job to police; membership growth within a known, filed,
not-yet-fixed cluster is bd 5038-1308j's concern, not a new regression
this gate exists to catch.

Reads the host repo's tree and ``.weld/discover.yaml``, neither of which
Bazel sees as an input -- ``external``, same reason and shape as the
whnwb/rhuc gates and ``weld_bazel_loads_repo_test.py``. Also *runs*
discovery rather than only resolving config, so it is one of the more
expensive members of that family (a full discover of this repo,
single-digit seconds); paid once per test-target invocation in
``setUpClass``, not per test case.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from weld.arch_lint_cycles import rule_no_circular_deps

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Pre-existing, filed, real file-level depends_on/contains cycles this gate
# ratchets against instead of silently re-widening NON_STRUCTURAL_EDGE_TYPES
# to hide them, or leaving the new gate permanently red. Keyed by the
# violation's anchor node id (the SCC's lowest-sorted member -- the same id
# `wd lint --json` reports as `node_id`), stable across two independent
# fresh-discover runs measured during ojg27. Full detail, member counts, and
# root-cause reasoning: bd 5038-1308j. Disposition (break/accept) and the
# reasoning behind each: ADR 0130. Trim an entry the moment its cycle is
# genuinely broken -- leaving a stale entry here fails
# test_baseline_has_no_stale_entries below. Eight entries are gone for that
# reason, not because the graph changed shape: ``file:weld/_warm_cli`` (the
# 14th entry; ADR 0130 broke it by parameterizing ``_warm_cli.build_parser``
# instead of importing its two defaults from ``weld.warm``),
# ``file:weld/_contract_validators`` (bd 5038-l24d9: ``ValidationError`` +
# the vocabulary constants moved out of ``contract.py`` into the
# dependency-free ``weld/_contract_types.py`` leaf),
# ``file:tools/tier1_corpus`` (bd 5038-bv76d: CorpusEntry/LanguagePins moved
# to the dependency-free leaf ``tools/_tier1_corpus_types.py``, which both
# siblings import instead of the ops module reaching back under
# ``TYPE_CHECKING``), ``file:weld/_mcp_dispatch`` (bd 5038-dokwz:
# ``dispatch()``/``run_stdio()`` take the live tool registry as an explicit
# ``tools_provider`` parameter instead of importing it back from
# ``weld.mcp_server``), ``file:weld/agent_graph_metadata`` (bd
# 5038-ujv26: ``ParsedAgentGraphAsset``, ``DerivedAgentGraphNode``, and the
# four private extraction helpers moved into the dependency-free
# ``weld/_agent_graph_asset.py`` leaf, which both the metadata module and
# its TOML sibling import instead of reaching back into each other),
# ``file:weld/bench/bench_cli`` (bd 5038-4le0k: ``runner.py``'s legacy
# ``main()`` shim deleted entirely; its two real external callers --
# ``weld/cli.py``'s ``wd bench`` dispatch and ``weld/bench/__main__.py``'s
# ``py_binary`` entry point -- now import ``weld.bench.bench_cli.main``
# directly, same convention as ``weld/discover.py``/``weld/_discover_cli.py``),
# and ``file:weld/_graph_closure_invariants`` -- two tasks, not one: bd
# 5038-mx8sd moved ``Violation`` into the dependency-free leaf
# ``weld/_arch_lint_types.py`` (the six rule modules that late-imported it
# from ``weld.arch_lint`` now import it from the leaf at real top level),
# dissolving that exact 8-member SCC, but a fresh discovery afterward
# surfaced a smaller 2-member SCC that anchor had been co-hiding:
# ``weld/arch_lint`` <-> ``weld/arch_lint_cli`` over ``available_rule_ids``/
# ``lint`` -- a registry/runner dependency, not a shared type. bd 5038-efr7z
# then broke that residual too via dependency injection (ADR 0130
# disposition #7, the shape ``weld/_mcp_dispatch`` used):
# ``arch_lint_cli.main()`` takes the registry as required keyword-only
# parameters instead of importing it back, so ``file:weld/arch_lint`` is now
# a clean removal like the rest, not a re-anchor. And
# ``file:weld/_file_index_incremental`` (bd 5038-kxx79: the one real,
# non-accidental cycle in the batch -- ``file_index.build_file_index``
# reached *up* into ``_file_index_incremental.tokens_for_file`` for the one
# tokenizer both the full walk and the incremental patch path need;
# ``tokens_from_content``/``tokens_for_file`` moved *down* into
# ``weld/file_index.py``, which already owned ``build_file_index`` and
# re-exported the raw per-extension extractors, so
# ``_file_index_incremental.py`` is now a clean one-directional consumer of
# ``file_index.py``'s build/save/load surface). All 8 of ADR 0130's
# worth-breaking follow-ups are now done; only the 5 ACCEPT entries remain.
_KNOWN_PRE_EXISTING_CYCLE_ANCHORS: frozenset[str] = frozenset({
    # ACCEPT (ADR 0130): registry-hub test cluster, decomposing ~66 files is
    # disproportionate to this ratchet's purpose.
    "file:tools/_tier_check_framework_markers_test",
    # ACCEPT (ADR 0130): package/registry-hub shape -- confirmed
    # weld/viz/__init__.py has zero submodule imports of its own; every
    # member here only shares an edge with the 7-line package init.
    "file:weld/viz/_adapter_helpers",
    # ACCEPT (ADR 0130, re-anchored by bd 5038-uuxaz.6-repair): a genuine,
    # pre-existing 5-member depends_on cycle among python_callgraph and its
    # four AST-emission helpers (_python_decorates/_python_inherits/
    # _python_references/_python_scope_calls) -- each helper imports
    # python_callgraph at module top level for its shared symbol-id
    # helpers, and python_callgraph imports each helper back to dispatch
    # into it. Not new: this exact cycle was always present but had been
    # co-anchored (hidden under a single larger 7-member SCC) by a
    # *different*, now-correctly-excluded lazy edge --
    # python_strategies/_python_expr_resolve imports python_callgraph from
    # inside a function body "to avoid a circular import at module load
    # time" (its own docstring), the same sanctioned idiom this repo's
    # no-circular-deps rule now discounts as structural evidence
    # (uuxaz.6-repair: graph_closure marks that edge ``deferred``). Once
    # the lazy bridge stopped counting, the mixin cluster it used to be
    # fused to (_python_callgraph_visitor, _python_expr_resolve -- both
    # ACCEPT-worthy on their own merits, same "genuinely shared class
    # state" reasoning as the prior entry here) separated cleanly from
    # this real, unrelated cycle underneath. Decomposing the AST-emission
    # helper family out of python_callgraph is a real module-boundary
    # redesign (same disproportionate-cost shape as the other ACCEPT
    # entries), not a side effect this bug fix should attempt.
    "file:weld/strategies/_python_decorates",
})


class RealRepoNoCircularDepsTest(unittest.TestCase):
    """Against this repository's own discover.yaml and real strategies."""

    @classmethod
    def setUpClass(cls) -> None:
        # The published source tree carries the package but not the host
        # repo's own .weld/discover.yaml; skip cleanly there rather than
        # fail (same guard shape as weld_bazel_loads_repo_test.py).
        if not (_REPO_ROOT / ".weld" / "discover.yaml").is_file():
            raise unittest.SkipTest("host repo .weld/discover.yaml not present")
        from weld.discover import _discover_single_repo

        cls.graph = _discover_single_repo(
            _REPO_ROOT, incremental=False, with_sqlite=False, write_graph=False,
        )

    def _violations(self):
        nodes = self.graph.get("nodes", {})
        edges = self.graph.get("edges", [])
        # Guards the guard: an empty graph would make both assertions below
        # vacuously true.
        self.assertTrue(nodes, "real discovery produced no nodes")
        self.assertTrue(edges, "real discovery produced no edges")
        return list(rule_no_circular_deps({"nodes": nodes, "edges": edges}))

    def test_no_unbaselined_circular_deps(self) -> None:
        violations = self._violations()
        unexpected = [
            v for v in violations
            if v.node_id not in _KNOWN_PRE_EXISTING_CYCLE_ANCHORS
        ]
        messages = "\n".join(v.message for v in unexpected)
        self.assertEqual(
            [], unexpected,
            f"{len(unexpected)} new no-circular-deps violation(s) on the "
            f"real graph, outside the tracked pre-existing baseline "
            f"(bd 5038-1308j):\n{messages}",
        )

    def test_baseline_has_no_stale_entries(self) -> None:
        found_anchors = {v.node_id for v in self._violations()}
        stale = _KNOWN_PRE_EXISTING_CYCLE_ANCHORS - found_anchors
        self.assertEqual(
            set(), stale,
            f"{len(stale)} baselined no-circular-deps anchor(s) no longer "
            f"appear as violations on the real graph -- the underlying "
            f"cycle was fixed (or the graph changed shape); trim these "
            f"from _KNOWN_PRE_EXISTING_CYCLE_ANCHORS: {sorted(stale)}",
        )


if __name__ == "__main__":
    unittest.main()
