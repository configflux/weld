"""Full-vs-incremental discovery equivalence targets that need more than a line.

``weld/tests/BUILD.bazel`` holds the ``incremental_*_equivalence_test`` family
one declaration per line, which works for a suite whose members are a name, a
src, and the same three deps. Four kinds of member do not fit that shape and
live here instead (bd ``hpv7``'s extraction convention -- names, srcs, and deps
are unchanged, so every label still reads ``//weld/tests:<name>``):

* the **generative** member (bd ``scjs2``), whose srcs are the sweep modules
  rather than a single file;
* the **smoke tier** it expands into (bd ``br7jb``) -- one target per seed,
  declared by list comprehension, which BUILD.bazel cannot express on one
  line, plus the one target that checks what the seed set spans;
* the **merge-train sweep** (bd ``us0u9``) and the permanent negative control
  that makes its green verdict mean something -- two targets in two different
  lanes over one shared window module, and the only pair here whose
  declaration must carry a justification comment ADR 0139 requires by name,
  which a one-line declaration has nowhere to put;
* the three members that **declare grammar wheels** (bd ``uaz2d``), moved here
  with the comment that explains why they carry deps the rest do not.

Everything declared here is hermetic. All of it is untagged and runs in the
fast loop except the merge-train sweep, which carries ``integration`` as a cost
label under ADR 0139's narrow admission -- see the comment on that target.
"""

load("@rules_python//python:defs.bzl", "py_test")

# The seeded generator plus the runner/differ, carried by every target that runs
# a case. Both are ``_``-prefixed helpers, not tests in their own right.
_SWEEP_SRCS = ["_equivalence_sweep.py", "_equivalence_sweep_repo.py"]

_RUNTIME_STRATEGIES = ["//weld:runtime", "//weld/strategies", "//weld/strategies:helpers"]

_GRAMMARS = [
    "@pypi//tree_sitter",
    "@pypi//tree_sitter_go",
    "@pypi//tree_sitter_rust",
    "@pypi//tree_sitter_typescript",
]

# The smoke tier: a set that draws every ``_equivalence_sweep_repo.ROUNDS``
# entry, every ``IMPORT_SHAPES`` member, both glob splits, and cross-glob
# links. That property is not a comment: the coverage target below asserts it
# over this same tuple, which is why the tuple lives here and reaches Python
# only through ``env``. A second copy in a Python module would be a second
# source of truth for one fact.
#
# It was a bare diversity floor -- one seed per round, nothing spare -- until
# bd rwi34 gave it a regression seed to carry as well (below), so the set now
# spans the generator AND pins a case that escaped. Those are different jobs
# and the coverage assertion only polices the first, which is why the second
# is written down here.
#
# Each seed is EQUIVALENT today and costs about a second (two full discovers and
# one incremental refresh over a few generated files), so every target sits far
# inside ADR 0136 section 9's ten-second untagged budget.
#
# Seed 369 is here because it is the sweep's first real finding (bd rwi34 -- an
# incremental refresh keeping a never-walked stub alive after its sole importer
# is deleted, on the strength of a clean file's closure-derived depends_on). It
# was carried as an expectedFailure in the aggregate suite while the bug was
# open; ADR 0113's loop is that a fix moves it HERE, as an ordinary per-seed
# target, so the case that cost us something keeps running on every fast loop
# rather than the sweep merely going quiet. Its minimized cast is
# incremental_closure_anchored_stub_equivalence_test and the rule it exercises
# is discovery_state_closure_anchor_test; this target is the third pin, on the
# generated case itself.
#
# Spelled as whole target names rather than as bare seeds, because this repo
# reads its own BUILD graph and guards it: weld_bazel_loads_repo_test's
# "no target is invented into a package" check (bd akwh) requires a modelled
# target's name to appear literally in its package's own sources, since a name
# nothing in the package spells is indistinguishable from a wrong-package
# attribution. A computed name fails that check -- or, worse, escapes it by
# not being modelled at all, which is what ``%`` formatting does today
# (bd 5038-ojnx2). The seed is recovered from the name below, so a case is
# still declared in exactly one place and the two cannot disagree.
_SEED_PREFIX = "incremental_generative_seed_"

_SEED_SUFFIX = "_test"

SMOKE_SEED_TARGETS = (
    "incremental_generative_seed_1_test",
    "incremental_generative_seed_2_test",
    "incremental_generative_seed_3_test",
    "incremental_generative_seed_16_test",
    "incremental_generative_seed_172_test",
    "incremental_generative_seed_369_test",
)

def _seed_of(name):
    """The seed a per-seed target's own name carries."""
    if not name.startswith(_SEED_PREFIX) or not name.endswith(_SEED_SUFFIX):
        fail("a smoke seed target is named " + _SEED_PREFIX + "<seed>" +
             _SEED_SUFFIX + ", not: " + name)
    return name[len(_SEED_PREFIX):-len(_SEED_SUFFIX)]

def equivalence_tests():
    # bd scjs2, ADR 0139 mechanism 5: the generative half of the
    # incremental_*_equivalence_test family. Every other member is a fixture
    # written after a divergence escaped, so the suite enumerates the shapes we
    # already know; this one draws small multi-glob repos from a seed (import
    # shape x glob split x edit/delete round) and diffs the node+edge sets the
    # two discovery paths produce. What it asserts is the MECHANISM, not the
    # absence of bugs (bd us0u9 settles that: gating on a discovered divergence
    # rewards weakening the comparison) -- so the load-bearing tests are the
    # negative controls that prove run_case goes red on an injected difference,
    # plus the two self-checks that refuse a vacuous run (an incremental round
    # that silently degraded to a full discover, and a generated tree discovery
    # never saw). Its first 1400-seed sweep found bd rwi34, pinned there as an
    # expectedFailure per ADR 0113. No local env: .bazelrc's
    # --test_env=PYTHONHASHSEED=0 is inherited, and the seeding this file cares
    # about is its own explicit random.Random(seed), not the hash seed. The
    # ordinary per-seed equivalence assertion is the smoke tier below; the wide
    # integration-tagged sweep is bd us0u9's.
    py_test(
        name = "incremental_generative_equivalence_test",
        srcs = _SWEEP_SRCS + ["incremental_generative_equivalence_test.py"],
        deps = _RUNTIME_STRATEGIES,
    )

    # bd br7jb: one target per seed, not one target looping over the set.
    # The loop hides what Bazel would otherwise give free -- a cached case is
    # skipped rather than re-run, tools/changed_test_targets.py selects the
    # cases a diff touches, and a failure names its seed in the target label.
    # Each target carries only its OWN seed for the same reason: handing every
    # target the whole set would put every seed in each action key, so adding
    # one would invalidate all the others.
    [py_test(
        name = _name,
        srcs = _SWEEP_SRCS + ["incremental_generative_seed_case_test.py"],
        main = "incremental_generative_seed_case_test.py",
        env = {"WELD_SWEEP_SEED": _seed_of(_name)},
        deps = _RUNTIME_STRATEGIES,
    ) for _name in SMOKE_SEED_TARGETS]

    # bd us0u9: the merge-train sweep -- several hundred generated cases, the
    # far tier of the two ADR 0139 mechanism 5 describes.
    #
    # TAG JUSTIFICATION (docs/testing-hygiene.md "Which lane does my test
    # belong in?" item 3, second branch): this target is HERMETIC -- in-process
    # _discover_single_repo over generated temp repos, no ambient tooling, no
    # shelling out beyond `git` on a temp dir -- and carries `integration`
    # purely as a COST label. That is admissible only under ADR 0139
    # "Admitting the class-5 sweep to the --config=ci lane", which narrows
    # ADR 0070's definition to "excluded from the inner loop for cost, run in
    # full on the CI-parallel lane" and leaves `no-sandbox` as the hermeticity
    # marker. Its three admission conditions, answered:
    #   * deterministic -- the window is a fixed contiguous seed range and each
    #     case draws from an explicit random.Random(seed);
    #   * this comment cites that ADR;
    #   * runtime budget: ~65 s measured for the 400-seed window on a
    #     development machine, inside the one-to-two minutes ADR 0139 states.
    # `size = "large"` is the only non-default attribute: it takes the timeout
    # off the 300 s a medium target gets, since a loaded CI runner is a wide
    # multiple slower than the machine that 65 s was measured on and a timeout
    # flake here would be indistinguishable from a real divergence. It also
    # tells Bazel this is a minute of one core, which is true and is the one
    # thing the scheduler can act on. Bazel's own size-vs-runtime advisory will
    # suggest `size = "medium"` for a 68 s run; that is the trade being made
    # deliberately, since 300 s is under 5x the measured time and a timeout on
    # a contended runner would read as a divergence.
    #
    # Reached by `bazel test --config=ci`, and so by the repository gate at
    # its `--scope=ci` (the pre-push gate), NOT by a mid-session
    # `--scope=full`, which does not pass that config: ADR 0139 is
    # explicit that a divergence only this target can find surfaces before the
    # push that would carry it to origin/main, not at the merge that
    # introduced it. The pinned seeds -- the cases that have already cost us
    # something -- stay untagged above and run on every fast loop.
    py_test(
        name = "incremental_generative_sweep_test",
        size = "large",
        srcs = _SWEEP_SRCS + [
            "_equivalence_sweep_range.py",
            "incremental_generative_sweep_test.py",
        ],
        tags = ["integration"],
        deps = _RUNTIME_STRATEGIES,
    )

    # The sweep's permanent negative control (bd us0u9, ADR 0139: "a sweep that
    # silently stops comparing is green forever: class 1 in its purest form").
    # UNTAGGED on purpose, and that is the whole point of it being its own
    # target rather than a class in the file above: the sweep runs only at
    # --config=ci, but the machinery it guards is edited in the inner loop, so
    # a control sharing the sweep's lane would be absent from every lane where
    # the code changes. Two generated cases plus pure policy checks, so it
    # stays far inside ADR 0136 section 9's ten-second untagged budget.
    py_test(
        name = "incremental_generative_sweep_control_test",
        srcs = _SWEEP_SRCS + [
            "_equivalence_sweep_range.py",
            "incremental_generative_sweep_control_test.py",
        ],
        deps = _RUNTIME_STRATEGIES,
    )

    # The seed set's own negative control: pure generator draws, no discovery,
    # so it reads the whole set without paying for it. Needs only the generator
    # module, hence neither the runner nor the strategies.
    py_test(
        name = "incremental_generative_seed_coverage_test",
        srcs = [
            "_equivalence_sweep_repo.py",
            "incremental_generative_seed_coverage_test.py",
        ],
        env = {
            "WELD_SWEEP_SEEDS": ",".join(
                [_seed_of(_name) for _name in SMOKE_SEED_TARGETS],
            ),
        },
        deps = ["//weld:runtime"],
    )

    # bd uaz2d: these three equivalence suites really parse Go/Rust/TypeScript
    # through `wd discover` (their fixtures assert the baseline run mints real
    # symbols), so they declare their grammar wheels instead of inheriting the
    # no-wheels comprehension in BUILD.bazel -- public CI presents no ambient
    # tree-sitter, and the ambient interpreter is the only reason they ever
    # passed locally.
    py_test(
        name = "incremental_external_package_purge_equivalence_test",
        srcs = ["incremental_external_package_purge_equivalence_test.py"],
        deps = _RUNTIME_STRATEGIES + ["@pypi//tree_sitter", "@pypi//tree_sitter_go"],
    )

    [py_test(
        name = _name,
        srcs = [_name + ".py"],
        deps = _RUNTIME_STRATEGIES + _GRAMMARS,
    ) for _name in (
        "incremental_inherits_provenance_purge_test",
        "incremental_unresolved_symbol_purge_equivalence_test",
    )]
