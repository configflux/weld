"""Grammar preconditions for the ADR 0064 tier-check gate tests.

Two lanes, and the difference between them is the whole point of this
module.

**Lockfile lane** (``cpp``, ``go``, ``rust``, ``typescript``). The
grammar wheel is pinned in ``third_party/python/requirements_lock.txt``
and the gate target names it in ``deps``, so under Bazel the wheel is in
the runfiles or the build fails. A missing grammar there is regressed
wiring, not an environment fact, and :func:`require_grammar` fails
instead of skipping. Outside Bazel -- or where the caller declares a
tree-sitter blocker of its own (see :data:`_BLOCKER_ENV`) -- absence is
expected and skipping is correct.

**Ambient lane** (``csharp``, ``java``). ADR 0069 decided *not* to pin
``tree-sitter-c-sharp`` / ``tree-sitter-java``: two external
dependencies and their wheel-bump maintenance are not worth an
integration-flavoured assertion whose logic is already covered by the
mock-based group. Those gates therefore still skip when the ambient
grammar is absent, but :func:`require_ambient_grammar` makes the skip
*attributable* -- the reason cites the ADR and says the absence is
intended, so a reader can tell an accepted gap from broken wiring.

The split is **enforced, not documentary** (bd qjxe). Each helper
rejects a language belonging to the other lane -- and an unclassified
one -- before it probes anything, so a mis-wired gate raises on every
host rather than only where the grammar happens to be missing.
:data:`LOCKFILE_GRAMMARS` and :data:`AMBIENT_GRAMMARS` used to be read
by nothing but their own disjointness test, which left
``require_ambient_grammar("cpp")`` a silently accepted way to restore
the green skip below. ``//tools:tier_check_gate_lane_wiring_test``
carries the other half: that each gate target's declared wheels agree
with the helper its source calls.

Background (bd srzy, following bd c42b): all seven per-language
tier-check gates previously carried a class-level
``@unittest.skipUnless(TREE_SITTER_AVAILABLE, ...)`` and declared no
grammar wheels at all. They are ``no-sandbox`` tagged, so only
``--config=ci`` reaches them, and there they skipped green on every run
-- coverage that existed solely on developer hosts that happened to
carry the wheels on their user site. The Bazel sandbox does not withhold
the user site, which is how "hermetic" targets silently inherited
whatever the host had.

Cross-package home (bd 9txq, moved by bd fkmvv). This module also hosts
:func:`skip_or_fail_without_grammar`, the shared tail of this same
policy for the two callers that used to hand-roll an identical copy:
``weld/tests/weld_cpp_type_uses_test.py`` and
``weld/tests/bench/bench_grammar_precondition.py``. bd 9txq first placed
the shared module under ``tools/`` on the reasoning that ``weld/tests/``
ships in the public *wheel* no more than ``tools/`` does -- but the
boundary that matters is the public *repo tree*: ``weld/tests/`` ships
there and ``tools/`` is publishignored wholesale, so shipping targets
depending on ``//tools:tier_check_grammar_gate`` made the public
``bazel build //...`` fail at load time (it aborted the v0.23.0
release). The module therefore lives here, on the shipping side of the
publish boundary, and every consumer -- the ``tools/`` tier-check gates
included -- links ``//weld/tests:tier_check_grammar_gate`` and imports
the top-level ``tier_check_grammar_gate`` module its ``imports = ["."]``
exposes.
"""

from __future__ import annotations

import os
import sys
import unittest

# Languages whose grammar wheel is pinned in requirements_lock.txt. The
# lockfile also pins tree-sitter-python and tree-sitter-javascript;
# those simply have no per-language tier-check gate today.
LOCKFILE_GRAMMARS: frozenset[str] = frozenset(
    {"cpp", "go", "javascript", "python", "rust", "typescript"},
)

# Languages deliberately left out of the lockfile by ADR 0069.
AMBIENT_GRAMMARS: frozenset[str] = frozenset({"csharp", "java"})

# Where the deps that back the lockfile lane are declared. Named in the
# failure diagnostic so the fix needs no archaeology, and read as data by
# //tools:tier_check_gate_lane_wiring_test.
GATE_BZL = "tools/tier_check_gate_targets.bzl"

# The umbrella wheel every lockfile-lane gate needs alongside its own
# grammar. Grammar labels are derived, never passed in: see
# :func:`grammar_labels`.
UMBRELLA_LABEL = "@pypi//tree_sitter"

# Which helper owns which lane, for the diagnostic that redirects a
# mis-wired caller. Keyed by lane so both directions read off one map.
_LANE_HELPERS = {
    "lockfile": "require_grammar",
    "ambient": "require_ambient_grammar",
}

# The caller's word that it is running a tree-sitter blocker of its own.
# It is a declaration, not a mechanism, and it has **no producer in this
# tree**: ADR 0104 retired both halves of the lane that used to set it --
# the second `bazel test //...` invocation in the gate script and the
# `weld/__init__.py` meta-path finder that gave it teeth -- and
# //tools:local_gate_hermetic_lane_test pins both in their removed state.
# So do not go looking for the lane; setting this variable blocks nothing
# by itself.
#
# The read is kept anyway, deliberately (bd 2z8w, applying to every
# reader of it what ADR 0104's Consequences already recorded for
# `weld_cpp_type_uses_test`). Under Bazel the wheels are in the runfiles
# by declaration, so an externally supplied blocker is the only way to
# reach a missing grammar there -- and it is the one case where branch
# 4's "restore the deps" diagnostic would be wrong advice.
_BLOCKER_ENV = "WELD_HERMETIC_BLOCK_TREE_SITTER"


def grammar_module_name(language: str) -> str:
    """Return the grammar package module for *language*.

    Delegates to the strategy's own mapping rather than reimplementing
    the ``tree_sitter_{language}`` convention. That convention has
    exceptions -- ``csharp`` resolves to ``tree_sitter_c_sharp``, not
    ``tree_sitter_csharp`` -- and a gate that guessed the name would
    report a false absence for exactly the language ADR 0069 is about.
    Sharing the function also means the gate decides on the same name
    the parser will actually import.
    """
    from weld.strategies._ts_parse import grammar_module_name as _name

    return _name(language)


def _umbrella_available() -> bool:
    """True when the ``tree_sitter`` umbrella package is importable.

    Read through ``weld.strategies.tree_sitter`` rather than by probing
    ``import tree_sitter`` directly, so the gate decides on the very
    constant the strategy itself branches on -- including when a harness
    has blocked the import out from under both.
    """
    try:
        from weld.strategies.tree_sitter import TREE_SITTER_AVAILABLE
    except Exception:
        return False
    return bool(TREE_SITTER_AVAILABLE)


def grammar_available(language: str) -> bool:
    """True when both the umbrella and the *language* grammar import.

    Never raises: a gate calls this from ``setUpClass``, where an
    escaping ``ImportError`` would surface as an error with a much worse
    diagnostic than the deliberate one below.
    """
    if not _umbrella_available():
        return False
    try:
        from weld.strategies._ts_parse import (
            grammar_available as _probe,
        )
    except Exception:
        return False
    try:
        return bool(_probe(language))
    except Exception:
        # _probe narrows to ImportError/ModuleNotFoundError; anything
        # else (a grammar whose import runs broken module-level code)
        # must still read as "absent" rather than escape setUpClass.
        return False


def _lane_of(language: str) -> str | None:
    """Which lane *language* is classified into, or ``None``."""
    if language in LOCKFILE_GRAMMARS:
        return "lockfile"
    if language in AMBIENT_GRAMMARS:
        return "ambient"
    return None


def _require_lane(language: str, *, lane: str) -> None:
    """Reject *language* unless it is classified into *lane*.

    Checked before the availability probe, deliberately. A mis-wired
    gate is a wiring bug whether or not the host happens to carry the
    grammar, and under Bazel the lockfile wheels are in the runfiles by
    declaration -- so a lane check behind the ``available``
    short-circuit would never fire on the machine that matters. That is
    the same shape of hole as a gate that only ever skips.

    ``ValueError`` rather than ``AssertionError`` keeps the two failure
    classes distinguishable by type: this one says "you called the wrong
    helper", branch 4 of :func:`require_grammar` says "your deps
    regressed". Neither is ever a :class:`unittest.SkipTest`, because a
    skip would colour the mistake green.
    """
    actual = _lane_of(language)
    if actual == lane:
        return
    if actual is None:
        raise ValueError(
            f"{language!r} is in neither LOCKFILE_GRAMMARS nor "
            f"AMBIENT_GRAMMARS. Classify it before gating it: a lockfile "
            f"language declares its wheel in {GATE_BZL} and fails on "
            "absence, an ADR 0069 ambient language declares none and "
            "skips attributably.",
        )
    raise ValueError(
        f"{language!r} belongs to the {actual} lane, so "
        f"{_LANE_HELPERS[lane]}() is the wrong precondition for it -- "
        f"call {_LANE_HELPERS[actual]}() instead. Using the lockfile "
        "helper on an ambient language hard-fails CI for a grammar ADR "
        "0069 deliberately leaves unpinned; using the ambient helper on "
        "a lockfile language restores the green skip that hid seven "
        "inert gates (bd srzy).",
    )


def grammar_labels(language: str) -> tuple[str, ...]:
    """The ``@pypi//`` deps a lockfile-lane gate must declare.

    Derived from :func:`grammar_module_name` rather than passed in by
    each caller. The labels used to be a tuple every gate wrote out by
    hand, duplicating its own BUILD ``deps`` with nothing checking that
    the two agreed -- and a stale copy sends the fixer reading branch
    4's diagnostic to the wrong place. One derivation means there is
    nothing left to disagree; //tools:tier_check_gate_lane_wiring_test
    checks it against what the targets actually declare.

    Raises :class:`ValueError` for an ambient language: ADR 0069 keeps
    those out of the lockfile, so there is no ``@pypi//`` target to
    name and inventing one would be worse than refusing.
    """
    _require_lane(language, lane="lockfile")
    return (UMBRELLA_LABEL, f"@pypi//{grammar_module_name(language)}")


def skip_or_fail_without_grammar(
    *,
    reason: str,
    labels: tuple[str, ...],
    declared_in: str,
) -> None:
    """The shared tail of the skip-or-fail-on-missing-grammar policy.

    Three preconditions in this tree used to hand-roll an identical
    three-branch tail once something was confirmed missing: this
    module's own :func:`require_grammar`, ``weld_cpp_type_uses_test``'s
    real-parse gate, and ``bench_grammar_precondition``'s bench gate (bd
    c42b, bd srzy, bd gjli respectively). They differed only in which
    BUILD file the failure named and which grammar(s) the reason
    described -- and that difference is exactly what let one of them
    once name the wrong BUILD file, the defect class bd 9txq
    consolidates this against.

    *reason* and *labels* stay caller-composed rather than derived from
    a single ``language`` parameter here, because how "missing" is
    decided legitimately differs per caller: one grammar for a
    :func:`require_grammar` language, versus however many of several
    specific modules failed to import for the bench gate. That
    difference is the one thing this function must NOT paper over, so it
    stays with the caller that can actually tell missing umbrella from
    missing grammar. *declared_in* is the one thing every caller must
    supply for itself: the BUILD file where ITS OWN target declares
    *labels* -- never a neighbour's.

    Callers only reach this once they have already decided the grammar
    IS missing; there is no "return silently" branch here, because how
    that decision gets made is exactly the part that must stay local.

    1. blocker env (``WELD_HERMETIC_BLOCK_TREE_SITTER=1``) declared ->
       skip. The variable has no producer in this tree -- ADR 0104
       retired the lane that used to set it -- so the read is kept only
       as an operator's escape hatch (bd 2z8w).
    2. not running under Bazel (no ``TEST_SRCDIR``) -> skip. Off the
       Bazel path the wheels are a host fact, not a wiring promise.
    3. else (under Bazel, no blocker) -> fail. The caller's target
       declares *labels* as deps, so under Bazel they are in the
       runfiles or the build fails; a green skip here is exactly what
       this policy exists to prevent (bd srzy, bd gjli).

    Raises rather than returns a verdict, and does so directly rather
    than through a ``case: TestCase`` parameter: every existing caller
    reaches this from a ``setUp``/``setUpClass`` context, and
    ``unittest.TestCase.fail``/``skipTest`` are themselves nothing more
    than ``raise self.failureException(...)`` / ``raise SkipTest(...)``
    -- no ``TestCase`` in this tree overrides ``failureException`` -- so
    raising here reproduces every prior call site exactly, including the
    ``setUpClass`` classmethod one that has no ``self`` to route through.
    """
    if os.environ.get(_BLOCKER_ENV) == "1":
        raise unittest.SkipTest(
            f"{reason}; a caller-supplied blocker is declared "
            f"({_BLOCKER_ENV}=1). Nothing in this tree sets that variable "
            "(ADR 0104), so this skip means an operator asked for it, not "
            "that the deps regressed",
        )
    if not os.environ.get("TEST_SRCDIR"):
        raise unittest.SkipTest(f"{reason}; not running under Bazel")

    raise AssertionError(
        f"{reason} for {sys.executable}, but this target declares "
        f"{' + '.join(labels)} as deps, so they must be in its runfiles. "
        f"Restore them in {declared_in} -- a green skip here is exactly "
        "what this gate exists to prevent (bd srzy, bd gjli).",
    )


def require_grammar(
    language: str,
    *,
    available: bool | None = None,
) -> None:
    """Precondition for a gate whose grammar comes from the lockfile.

    Skips only where absence is legitimate; otherwise raises
    :class:`AssertionError` naming the interpreter that failed to
    resolve the grammar and the Bazel labels to restore -- derived by
    :func:`grammar_labels`, so the diagnostic cannot name deps the
    target does not have. The skip-or-fail branching itself is
    :func:`skip_or_fail_without_grammar`; this function's own job is
    composing *this lane's* reason and labels from *language*.

    An ambient language reaches none of that: it raises
    :class:`ValueError`, because failing the build over a grammar ADR
    0069 deliberately left unpinned is not this gate's call to make.

    *available* overrides the probe (tests inject both polarities).
    """
    labels = grammar_labels(language)
    if available is None:
        available = grammar_available(language)
    if available:
        return

    module = grammar_module_name(language)
    reason = f"{module} (or the tree_sitter umbrella) is not importable"
    skip_or_fail_without_grammar(
        reason=reason, labels=labels, declared_in=GATE_BZL,
    )


def require_ambient_grammar(
    language: str,
    *,
    available: bool | None = None,
) -> None:
    """Precondition for a gate whose grammar ADR 0069 left unpinned.

    Still a skip when the grammar is absent -- that is the accepted
    outcome, not a defect -- but an attributable one. The reason names
    the ADR and states the absence is intended, so this skip cannot be
    mistaken for the dropped-dep regression :func:`require_grammar`
    exists to catch.

    A lockfile language reaches none of that: it raises
    :class:`ValueError`, because skipping one of those is precisely the
    outcome its wheels were pinned to make impossible.
    """
    _require_lane(language, lane="ambient")
    if available is None:
        available = grammar_available(language)
    if available:
        return

    module = grammar_module_name(language)
    raise unittest.SkipTest(
        f"{module} is not importable; this is an ADR 0069 accepted gap, "
        f"not a wiring regression -- the {language} grammar is "
        "deliberately absent from third_party/python/requirements_lock.txt, "
        "so this gate contributes coverage only on hosts carrying the "
        "grammar ambiently. See docs/adrs/"
        "0069-csharp-java-real-parse-no-sandbox-lane.md.",
    )


__all__ = [
    "AMBIENT_GRAMMARS",
    "GATE_BZL",
    "LOCKFILE_GRAMMARS",
    "UMBRELLA_LABEL",
    "grammar_available",
    "grammar_labels",
    "grammar_module_name",
    "require_ambient_grammar",
    "require_grammar",
    "skip_or_fail_without_grammar",
]
