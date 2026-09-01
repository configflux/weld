"""The generative half of the incremental-equivalence family (ADR 0139 § 5).

Every other ``incremental_*_equivalence_test`` enumerates a shape somebody
wrote down after it escaped. This one asserts the *mechanism* that finds the
next shape works: :mod:`weld.tests._equivalence_sweep_repo` draws a repo from a
seed, :mod:`weld.tests._equivalence_sweep` runs it through both discovery paths
and diffs them, and what follows checks the four properties that make a run
usable as evidence.

The family's *ordinary* assertion -- full and incremental agree on a given
seed -- is deliberately not here. ``weld/tests/equivalence_tests.bzl``
expands it into one untagged Bazel target per seed (bd ``br7jb``), so each
case caches, gets selected, and reports under its own label. This file keeps
the mechanism, and one control seed to exercise it on.

Acceptance here is *mechanism-works*, not *found-a-real-bug* -- bd ``us0u9``
settles that, and for the reason it gives: gating on a discovered divergence
rewards sitting on the issue or weakening the comparison. So the load-bearing
tests are the negative controls. A comparison that has quietly stopped
comparing is green forever, which is ADR 0139 mechanism 1 in its purest form,
and the only defence is proving the same code path goes red on an injected
difference and refuses a run that could not have meant anything.

The doctored graphs below are produced by *doctoring a real discovery output*,
never by writing one out: an injected difference the producer could not have
emitted would prove the comparison detects a shape that never occurs.

Seed selection: seeds 1..1400 were swept during development. Seven diverged,
all of one class -- bd ``rwi34``, a never-walked stub that survives the
deletion of its sole importer because a clean file's closure-derived
``depends_on`` still names it. That is fixed, and per ADR 0113 the finding's
artifact is a *pinned case*, not a sweep that stopped reporting: seed 369 is
now its own per-seed target in ``weld/tests/equivalence_tests.bzl``'s smoke
tier, the minimized five-file cast is
``incremental_closure_anchored_stub_equivalence_test``, and the anchor rule
itself is ``discovery_state_closure_anchor_test``. The other six seeds are
recorded on the issue rather than here, so the fast loop pays for one case and
not seven. No known divergence is open against this generator today; the next
one is pinned here as an expected failure the same way, which is what keeps a
finding from quietly becoming a defended defect (ADR 0139 mechanism 2).
"""

from __future__ import annotations

import contextlib
import io
import subprocess
import tempfile
import unittest
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from weld.tests import _equivalence_sweep as sweep
from weld.tests._equivalence_sweep_repo import IMPORT_SHAPES, ROUNDS, generate_case

#: The case every mechanism test below runs on. One seed rather than a set:
#: the per-seed equivalence targets own that assertion now (bd ``br7jb``),
#: and a list here as well as in ``equivalence_tests.bzl`` would be two
#: sources of truth for which seeds the smoke tier runs. The controls, the
#: self-checks, the render-determinism check, and the CLI each need one
#: fixed case to run on, and this is it -- a ``delete_package`` round.
CONTROL_SEED = 2

#: The span the reachability checks below draw from. Wide enough that every
#: shape and every round is drawn, small enough to stay a pure-Python loop.
SPAN = range(1, 200)


@lru_cache(maxsize=None)
def _report(seed: int) -> sweep.DiffReport:
    """One real run per seed per process; the classes below share them."""
    return sweep.run_case(generate_case(seed))


@lru_cache(maxsize=None)
def _full_graph(seed: int) -> dict:
    return sweep.full_graph(generate_case(seed))


class GeneratorDeterminismTest(unittest.TestCase):
    """The generator draws from an explicitly seeded RNG and nothing else."""

    def test_same_seed_draws_an_identical_case(self) -> None:
        for seed in (1, 7, 99):
            with self.subTest(seed=seed):
                self.assertEqual(generate_case(seed), generate_case(seed))

    def test_different_seeds_draw_different_cases(self) -> None:
        """A generator that ignores its seed would satisfy every other test."""
        summaries = {generate_case(seed).summary() for seed in SPAN}
        self.assertGreater(len(summaries), len(SPAN) // 2)

    def test_every_import_shape_is_reachable(self) -> None:
        """A shape nothing ever draws is coverage the sweep does not have."""
        drawn = {
            shape
            for seed in SPAN
            for _, _, shape in generate_case(seed).links
        }
        self.assertEqual(drawn, set(IMPORT_SHAPES))

    def test_every_round_is_reachable(self) -> None:
        drawn = {generate_case(seed).round for seed in SPAN}
        self.assertEqual(drawn, set(ROUNDS))

    def test_cases_span_both_glob_splits_and_cross_glob_links(self) -> None:
        """Two- and three-glob repos, and links that cross a glob boundary.

        The cross-glob link is the whole reason for a multi-glob generator:
        bd ``yhz70`` is the divergence that only exists when a call's target
        sits in a glob its caller's does not own.
        """
        widths, crossings = set(), 0
        for seed in SPAN:
            case = generate_case(seed)
            widths.add(len(case.declared))
            crossings += any(
                case.packages[caller].root != case.packages[target].root
                for caller, target, _ in case.links
            )
        self.assertEqual(widths, {2, 3})
        self.assertGreater(crossings, len(SPAN) // 4)


class ReportDeterminismTest(unittest.TestCase):
    """Same seed, two independent runs, byte-identical rendered report.

    ``.bazelrc`` pins ``PYTHONHASHSEED=0`` for every test in this repo, which
    is why this assertion has to be made over two real runs rather than over
    two renders of one: the contract is the explicit ``random.Random(seed)`` in
    weld.tests._equivalence_sweep_repo, and a fixed hash seed would mask an
    unseeded draw rather than expose it (ADR 0139 § 5).
    """

    def test_two_runs_of_one_seed_render_identically(self) -> None:
        seed = CONTROL_SEED
        first = sweep.run_case(generate_case(seed)).render()
        second = sweep.run_case(generate_case(seed)).render()
        self.assertEqual(first, second)

    def test_the_render_names_the_seed_and_a_repro_command(self) -> None:
        report = _report(CONTROL_SEED)
        self.assertIn(f"seed {report.seed}:", report.render())
        self.assertIn(sweep.REPRO_TEMPLATE.format(seed=report.seed), report.render())


class NegativeControlTest(unittest.TestCase):
    """A doctored divergence is detected, through the real ``run_case`` path.

    bd ``us0u9`` requires this control permanently, not as a throwaway: it is
    the only assertion that distinguishes "the sweep found nothing" from "the
    sweep compares nothing".
    """

    SEED = CONTROL_SEED

    def test_the_undoctored_case_is_equivalent(self) -> None:
        """The control's other half: the same case is green when untouched."""
        self.assertFalse(_report(self.SEED).divergent)

    def test_a_dropped_node_is_reported(self) -> None:
        dropped: list[str] = []

        def sabotage(graph: dict) -> None:
            victim = sorted(graph["nodes"])[0]
            dropped.append(victim)
            del graph["nodes"][victim]

        report = sweep.run_case(generate_case(self.SEED), sabotage=sabotage)
        self.assertTrue(report.divergent)
        self.assertEqual(list(report.nodes_only_full), dropped)
        rendered = report.render()
        self.assertIn(dropped[0], rendered)
        self.assertIn(f"seed {self.SEED}:", rendered)
        self.assertIn(sweep.REPRO_TEMPLATE.format(seed=self.SEED), rendered)

    def test_a_dropped_edge_is_reported(self) -> None:
        dropped: list[tuple[str, str, str]] = []

        def sabotage(graph: dict) -> None:
            victim = min(sweep.edge_triples(graph))
            dropped.append(victim)
            graph["edges"] = [
                edge
                for edge in graph["edges"]
                if (edge["from"], edge["to"], edge["type"]) != victim
            ]

        report = sweep.run_case(generate_case(self.SEED), sabotage=sabotage)
        self.assertTrue(report.divergent)
        self.assertEqual(list(report.edges_only_full), dropped)
        self.assertIn(sweep._render_edge(dropped[0]), report.render())

    def test_an_added_node_is_reported_on_the_incremental_side(self) -> None:
        """Divergence is symmetric: an extra node is as much a finding.

        The re-export retarget (bd ``1m1g9``'s neighbour) produced exactly this
        asymmetry -- a stub one path minted and the other did not.
        """
        def sabotage(graph: dict) -> None:
            donor = sorted(graph["nodes"])[0]
            graph["nodes"]["symbol:unresolved:sweep_control"] = graph["nodes"][donor]

        report = sweep.run_case(generate_case(self.SEED), sabotage=sabotage)
        self.assertEqual(
            list(report.nodes_only_incremental), ["symbol:unresolved:sweep_control"]
        )
        self.assertIn("nodes only in incremental (1):", report.render())


class SelfCheckTest(unittest.TestCase):
    """The two ways a run can be vacuous are refused, not reported green."""

    def test_a_refresh_that_degrades_to_a_full_discover_is_refused(self) -> None:
        """No prior state means no incremental basis, so the branch never runs.

        This is the failure that would make every seed agree forever, since
        both sides would then be the same full-discovery code path.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sweep._init_repo(root)
            (root / ".weld").mkdir()
            (root / ".weld" / "discover.yaml").write_text(
                "sources: []\n", encoding="utf-8"
            )
            subprocess.run(
                ["git", "add", "-A"], cwd=str(root), check=True, capture_output=True
            )
            subprocess.run(
                ["git", "commit", "-qm", "x"],
                cwd=str(root), check=True, capture_output=True,
            )
            with self.assertRaises(sweep.SweepSelfCheckError):
                sweep._refresh_incrementally(root)

    def test_a_graph_missing_a_generated_module_is_refused(self) -> None:
        """A misdrawn glob would leave two empty graphs agreeing perfectly."""
        case = generate_case(CONTROL_SEED)
        graph = dict(_full_graph(case.seed))
        nodes = dict(graph["nodes"])
        victim = sorted(n for n in nodes if n.startswith("file:"))[0]
        del nodes[victim]
        graph["nodes"] = nodes
        with self.assertRaises(sweep.SweepSelfCheckError) as caught:
            sweep._check_population(case, graph)
        self.assertIn(victim, str(caught.exception))


class CommandLineTest(unittest.TestCase):
    """The standalone entry point ADR 0139 § 5's repro command names."""

    def test_a_single_seed_run_prints_its_report_and_exits_zero(self) -> None:
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            status = sweep.main(["--seed", str(CONTROL_SEED)])
        self.assertEqual(status, 0)
        self.assertEqual(stream.getvalue(), _report(CONTROL_SEED).render()
                         + "divergent cases: 0\n")

    def test_sweep_streams_rather_than_batching(self) -> None:
        """A divergence in the first seeds must not wait on the last thousand.

        bd ``us0u9``'s merge-train sweep is a minute or two of seeds; batching
        them into a list would hold every report until the run ended and lose
        all of them if one seed raised.
        """
        stream = sweep.sweep([CONTROL_SEED])
        self.assertIsInstance(stream, Iterator)
        self.assertEqual(next(stream).seed, CONTROL_SEED)

    def test_quiet_suppresses_equivalent_cases(self) -> None:
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            status = sweep.main(
                ["--base-seed", str(CONTROL_SEED), "--count", "1", "--quiet"]
            )
        self.assertEqual(status, 0)
        self.assertEqual(stream.getvalue(), "divergent cases: 0\n")


if __name__ == "__main__":
    unittest.main()
