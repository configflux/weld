"""The cpp grammar precondition shared by the bench weld-adapter tests.

A precondition that probes less than its diagnostic claims is worse than
no precondition at all: it reads as enforcement in review and enforces
nothing at runtime. That was bd gjli. The guard below probed only the
``tree_sitter`` **umbrella** via ``weld_adapter._is_tree_sitter_available``
while its failure message told the reader it was holding
``@pypi//tree_sitter`` *and* ``@pypi//tree_sitter_cpp`` in place -- so
dropping ``tree_sitter_cpp`` from the deps left the guard silent and the
cpp cases running with no cpp extraction behind them. It probes both now,
and the labels it names are derived from the modules it actually imports
(:data:`REQUIRED_LABELS`), so the two cannot drift apart again.

Why this module did not simply call into ``tier_check_grammar_gate``
from the start. That module's :func:`require_grammar` implemented the same
skip-or-fail policy for the same lockfile-lane grammar hard-coded to its own
diagnostic, ending in "Restore them in
``tools/tier_check_gate_targets.bzl``" -- which is where the *tier-check*
gate targets declare their wheels, not where the bench targets in
:data:`DECLARED_IN` do. Calling it would have bought deduplication at
the price of sending a fixer to a BUILD file that has nothing to do with
the failure -- a guard pointing at the wrong wheel declaration is the
very defect one layer over, and the reason bd gjli exists.

bd 9txq made ``declared_in`` a parameter of the shared branch policy
(``tier_check_grammar_gate.skip_or_fail_without_grammar``)
instead, which is what :func:`skip_or_fail_without_grammars` below now
calls: this module still owns *which* modules it probes and how it
composes its own reason, but the branch order (blocker env -> skip; not
under Bazel -> skip; else -> fail) and the fail message no longer have a
copy here. See that function's docstring for why ``declared_in`` beats
folding this module's probe into the shared one.

Both callers live in ``weld/tests/bench/BUILD.bazel``, which is what
makes a single :data:`DECLARED_IN` constant honest here.
"""

from __future__ import annotations

import unittest

from weld.bench.adapters import weld as weld_adapter
from weld.strategies._ts_parse import grammar_available, grammar_module_name

# The skip-or-fail branch policy itself (bd 9txq consolidated it out of
# this module; see tier_check_grammar_gate's docstring for the
# placement rationale and that function's docstring for why declared_in
# is a parameter rather than another hard-coded BUILD path).
import tier_check_grammar_gate as grammar_gate

# The bench cases this guard fronts are all cpp: the extraction path
# whose silent no-op produced the F1=0.00 rows in the first place.
LANGUAGE = "cpp"

# Derived, never spelled out. ``grammar_module_name`` owns the
# ``tree_sitter_{language}`` convention *including* its exceptions
# (``csharp`` resolves to ``tree_sitter_c_sharp``), so a guard that
# guessed would report a false absence for the one language where the
# convention breaks. Deriving also means the module we import and the
# label we tell the reader to restore come from one place.
GRAMMAR_MODULE = grammar_module_name(LANGUAGE)
UMBRELLA_MODULE = "tree_sitter"
REQUIRED_LABELS = (f"@pypi//{UMBRELLA_MODULE}", f"@pypi//{GRAMMAR_MODULE}")

# Where the callers' deps are declared -- named in the diagnostic so the
# fix needs no archaeology.
DECLARED_IN = "weld/tests/bench/BUILD.bazel"

# The caller's word that it is running a tree-sitter blocker of its own.
# It has **no producer in this tree**: ADR 0104 retired both halves of
# the lane that used to set it, and //tools:local_gate_hermetic_lane_test
# pins them in their removed state. The read is kept anyway, deliberately
# (bd 2z8w): under Bazel the wheels are in the runfiles by declaration,
# so an externally supplied blocker is the only way to reach a missing
# grammar there -- and it is the one case where "restore the deps" would
# be wrong advice.
BLOCKER_ENV = "WELD_HERMETIC_BLOCK_TREE_SITTER"


def missing_grammar_modules() -> tuple[str, ...]:
    """Which of the two required tree-sitter modules will not import.

    Empty when both resolve. The umbrella is probed through
    ``weld_adapter._is_tree_sitter_available`` rather than a bare
    ``import tree_sitter`` so the guard decides on the very function the
    adapter under test branches on; the grammar goes through
    :func:`weld.strategies._ts_parse.grammar_available`, which is what
    ``load_ts_language`` will consult at parse time.

    A grammar whose import raises something other than ``ImportError``
    is deliberately *not* swallowed here: that is a broken wheel, not an
    absent one, and its own traceback names the culprit far better than
    this module's "restore the deps in BUILD.bazel" would.
    """
    missing = []
    if not weld_adapter._is_tree_sitter_available():
        missing.append(UMBRELLA_MODULE)
    if not grammar_available(LANGUAGE):
        missing.append(GRAMMAR_MODULE)
    return tuple(missing)


def skip_or_fail_without_grammars(
    case: unittest.TestCase,
    *,
    missing: tuple[str, ...] | None = None,
) -> None:
    """Skip only where absence is legitimate; otherwise fail loudly.

    The c42b idiom (``weld_cpp_type_uses_test``), applied here for the
    reason bd srzy filed: the bench adapter targets are ``no-sandbox``,
    so only ``--config=ci`` reaches them, and there the grammar-gated
    cases skipped green on every run -- their coverage existed solely on
    hosts that happened to carry the grammar wheels on their user site.

    Those targets now name both wheels in ``deps``, so under Bazel they
    are in the runfiles or the build fails. A missing module there is
    regressed wiring, not a host fact, and must fail.

    *missing* overrides the probe. The seam exists because the branch
    policy below is the part that goes quietly wrong -- a guard is only
    as good as the branch it takes on absence, and absence is the one
    state a passing host cannot reach. ``bench_grammar_precondition_test``
    injects each polarity rather than hoping for a grammar-less machine.

    *case* is accepted for call-site stability (this stayed the public
    signature every caller in this package already uses) but is not
    forwarded anywhere: the shared branch policy below raises directly,
    which reproduces ``case.skipTest``/``case.fail`` exactly -- see
    ``skip_or_fail_without_grammar``'s docstring for why.
    """
    if missing is None:
        missing = missing_grammar_modules()
    if not missing:
        return

    grammar_gate.skip_or_fail_without_grammar(
        reason=f"{', '.join(missing)} not importable",
        labels=REQUIRED_LABELS,
        declared_in=DECLARED_IN,
    )
