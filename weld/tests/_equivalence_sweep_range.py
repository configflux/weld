"""The merge-train window of the equivalence sweep (ADR 0139, bd ``us0u9``).

:mod:`weld.tests._equivalence_sweep` runs one generated case and diffs the two
discovery paths. This module is the *policy* around a wide run of them: which
seeds the merge train sweeps, which it skips and on whose authority, and how a
run of several hundred cases turns into one verdict a reader can act on.

It is a separate module from the runner because two targets in two different
lanes share it -- ``incremental_generative_sweep_test`` (``integration``, the
``--config=ci`` lane) and ``incremental_generative_sweep_control_test``
(untagged, the fast loop). Putting the window in the runner would also push
that file past this repo's 400-line cap.

Why the window is a Python constant and not a Bazel ``env`` entry, unlike the
smoke tier's seeds (bd ``br7jb``): that tier expands one target per seed, so
Starlark has to know the set, and the ``env`` round-trip is what keeps the
macro and the coverage assertion reading one source. Here there is a single
target and nothing for Starlark to expand, and an exclusion has to carry prose
-- an issue id and the shape it was excluded for -- that a comma-joined
environment string cannot hold honestly.

**The exclusion ledger is the load-bearing part.** A wide sweep has exactly one
tempting failure mode: it goes red on a divergence that is already known and
already tracked, and the cheapest way to green is to narrow the window or
loosen the comparison. Both are silent, and a sweep that has quietly stopped
comparing is green forever -- ADR 0139 mechanism 1 in its purest form. So the
sanctioned escape is made the *expensive* one: an exclusion names a bd issue
and the node id that must still show up on the incremental side, and
``incremental_generative_sweep_test`` re-runs every excluded seed and asserts
it still diverges in exactly that shape. Adding an entry to silence a genuinely
new divergence therefore costs a filed issue; and when the cited bug is fixed,
the entry stops being true and turns the sweep red until it is removed. That is
ADR 0113's loop -- a finding is proven by a pinned case, never by a generator
going green -- applied to the skip list rather than to the finding.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TextIO

from weld.tests._equivalence_sweep import (
    DiffReport,
    Sabotage,
    SweepSelfCheckError,
    sweep,
)

#: First seed of the merge-train window, and how many follow it. Contiguous
#: rather than sampled: the window has to be identical on every run for the
#: target to be deterministic, which ADR 0139 makes an admission requirement
#: for carrying ``integration`` on a hermetic target.
#:
#: Seeds 1..400 measure 65 s standalone on a development machine, inside the
#: one-to-two-minute budget that ADR 0139 states for this target. The span is
#: drawn from the 1..1400 range bd ``rwi34`` characterised, so which seeds in
#: it diverge today is a recorded fact rather than a hope.
BASE_SEED = 1
WINDOW = 400

#: The floor a run has to clear to count as a sweep at all. Independent of
#: ``WINDOW`` on purpose: it is what notices the window itself collapsing --
#: a ``WINDOW`` quietly dropped to 1 leaves every assertion about the cases
#: that *did* run perfectly true, and the target green while searching almost
#: nothing.
MINIMUM_CASES = 300

#: The shape a bd issue suffix has in this tree (``rwi34``, ``5038-ojnx2``),
#: which is how every other citation here spells one. It rejects a blank or a
#: prose placeholder; it cannot tell a plausible id from a wrong one, and does
#: not try. What keeps an entry honest is the re-run in
#: ``incremental_generative_sweep_test``, not this pattern.
_ISSUE_ID = re.compile(r"^(?:[0-9]+-)?[a-z0-9]{4,8}$")

_REMEDY = """\
Reproduce a case with the repro command in its report above, then narrow it by
hand. Per ADR 0113 the artifact of a finding is a PINNED case -- add the seed to
SMOKE_SEED_TARGETS in weld/tests/equivalence_tests.bzl (bd br7jb) and the
minimized fixture to the enumerative incremental_*_equivalence_test family.
This sweep going green again is not evidence that anything was fixed.

Do NOT narrow the window or relax the comparison to get back to green. If the
divergence is a known, tracked bug, file or find its issue and add a
KnownDivergence to weld/tests/_equivalence_sweep_range.py, which records the
skip instead of hiding it."""


@dataclass(frozen=True)
class KnownDivergence:
    """One seed the window skips, plus the evidence that it may still skip it.

    ``node_only_in_incremental`` is not decoration: it is what makes the skip
    re-checkable. A seed excluded by number alone would keep being skipped for
    whatever reason it diverged *now*, so an unrelated regression on that seed
    would be swallowed by an exclusion written for something else.
    """

    seed: int
    issue: str
    node_only_in_incremental: str

    def cites_an_issue(self) -> bool:
        return bool(_ISSUE_ID.match(self.issue))


#: Every seed inside the window that is known to diverge today, and why.
#:
#: Empty, and that is the ledger working rather than the ledger unused. Its one
#: entry was bd ``rwi34`` -- an incremental refresh keeping a never-walked stub
#: alive after its sole importer is deleted, because a clean file's
#: closure-derived ``depends_on`` still named it. That is fixed, so the entry
#: stopped being true and came out; per ADR 0113 the finding lives on as pinned
#: cases (seed 369 in ``SMOKE_SEED_TARGETS``, the minimized cast in
#: ``incremental_closure_anchored_stub_equivalence_test``, the rule itself in
#: ``discovery_state_closure_anchor_test``), never as this target going green.
#:
#: An entry belongs here only while its cited bug is open, and every assertion
#: over this tuple is written to hold vacuously so an empty ledger is a legal
#: state rather than one that needs a placeholder to satisfy.
KNOWN_DIVERGENCES: tuple[KnownDivergence, ...] = ()


def window_seeds() -> tuple[int, ...]:
    """Every seed the window covers, excluded ones included."""
    return tuple(BASE_SEED + offset for offset in range(WINDOW))


def excluded_seeds() -> tuple[int, ...]:
    return tuple(known.seed for known in KNOWN_DIVERGENCES)


def swept_seeds() -> tuple[int, ...]:
    """The window minus the ledger: what the merge train actually runs."""
    skip = set(excluded_seeds())
    return tuple(seed for seed in window_seeds() if seed not in skip)


@dataclass(frozen=True)
class SweepOutcome:
    """What a window run found, and how it says so."""

    ran: tuple[int, ...]
    divergent: tuple[DiffReport, ...]

    def failure_message(self) -> str:
        """The whole verdict, seeds and repro commands included.

        Rendered from the reports themselves rather than summarised, because
        the reader of this string is looking at a CI log for a sweep they did
        not run and cannot cheaply re-run: everything needed to reproduce one
        case by hand has to be in it.
        """
        heading = (
            f"{len(self.divergent)} of {len(self.ran)} generated cases diverged "
            f"between full and incremental discovery."
        )
        renders = "".join(report.render() for report in self.divergent)
        return "\n".join([heading, "", renders, _REMEDY])


def run_window(
    seeds: Iterable[int],
    *,
    sabotage: Sabotage | None = None,
    report_to: TextIO | None = None,
) -> SweepOutcome:
    """Run every seed in *seeds* and collect the divergences.

    An empty *seeds* raises rather than returning a clean outcome, for the
    reason :class:`weld.tests._equivalence_sweep.SweepSelfCheckError` exists: a
    window that swept nothing satisfies every assertion a caller could make
    about it, so it must not be reachable as a pass.

    *sabotage* is forwarded to :func:`weld.tests._equivalence_sweep.sweep`,
    which is how bd ``us0u9``'s negative control reaches this aggregation
    through the real comparison instead of around it. Production callers leave
    it ``None``.

    Divergent reports are written to *report_to* as they are found. A window is
    a minute or more of work, and a seed that raises would otherwise take every
    finding before it down with the process.
    """
    seeds = tuple(seeds)
    if not seeds:
        raise SweepSelfCheckError(
            "the sweep window is empty -- a run over no seeds cannot diverge, "
            "so it would report green having compared nothing"
        )
    divergent: list[DiffReport] = []
    ran: list[int] = []
    for report in sweep(seeds, sabotage=sabotage):
        ran.append(report.seed)
        if report.divergent:
            divergent.append(report)
            if report_to is not None:
                report_to.write(report.render())
                report_to.flush()
    return SweepOutcome(ran=tuple(ran), divergent=tuple(divergent))
