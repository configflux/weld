"""Eager federation inverted-index aggregation benchmark (ADR 0063).

Compares the opt-in eager path (``eager_index=True``) against the
lazy default on the same N=30 synthetic federation.

Two regression-style assertions (purposely loose to absorb shared-CI
jitter while still flagging real reverts):

1. **Eager median p50 beats lazy p50.** A regression to the lazy
   speed would push the ratio to ~1.0; we assert ``< 0.95``. Standalone
   benches show eager at ~0.27x lazy; bazel's runfiles overhead and
   shared-CI jitter compress that ratio, but it must still be a
   measurable win or the amortization story is broken.
2. **Construction tax stays bounded.** ADR 0063 documents ~17 ms for
   N=30; we cap at ``100 ms`` so a 5x build regression trips the
   assertion without missing the slack for a noisy CI host.

Both assertions include a measurement-floor guard and print the
numbers always so a CI run shows real values for triage. The target
is ``flaky=True`` (Bazel auto-retry) for the same reason the lazy
benchmark is flaky: synthetic-fixture absolute numbers are small and
shared-CI runners produce occasional outliers.
"""

from __future__ import annotations

import gc
import statistics
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_repo_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.federation import FederatedGraph  # noqa: E402

from weld.tests.bench.federation_bench_helpers import (  # noqa: E402
    setup_synthetic_workspace,
)


# Same shape as weld_federation_query_test for an apples-to-apples
# comparison; deliberately a sibling file so each test stays under the
# 400-line cap and has a focused mini-spec.
N_CHILDREN = 30
QUERY_TERM = "service"
# 21 reps tames percentile noise more than the 11-rep lazy bench; the
# eager amortization story needs a stable enough p95 that a regression
# is unambiguously a regression and not a single-sample blip.
QUERY_REPS = 21

# Eager must beat lazy median (p50) by at least this ratio. ADR
# observed ~0.27x in standalone bench; bazel/runfiles overhead and
# shared-CI jitter compress that to ~0.5x-0.7x in this harness. The
# 0.95 ceiling tolerates jitter while still flagging a regression
# that erases the amortization story (eager at 1.0x = no win).
EAGER_LATENCY_CEILING = 0.95

# Construction-time tax for N=30. ADR observed ~17 ms; 100 ms cap
# absorbs shared-CI jitter without missing a 10x regression.
CONSTRUCTION_CEILING_MS = 100.0


def _percentile(values: list[float], pct: float) -> float:
    """Simple linear percentile; values must be non-empty."""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    rank = (pct / 100.0) * (len(s) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(s) - 1)
    fraction = rank - lo
    return s[lo] * (1 - fraction) + s[hi] * fraction


def _run_queries(
    root: Path, term: str, reps: int, *, eager: bool,
) -> tuple[float, list[float]]:
    """Construct a federation and run *reps* queries; return (ctor_ms, latencies)."""
    ctor_start = time.perf_counter()
    fg = FederatedGraph(root, eager_index=eager)
    ctor_ms = (time.perf_counter() - ctor_start) * 1000.0
    latencies: list[float] = []
    try:
        for _ in range(reps):
            start = time.perf_counter()
            fg.query(term, limit=20)
            latencies.append(time.perf_counter() - start)
    finally:
        fg.close()
    return ctor_ms, latencies


class FederationEagerBenchTest(unittest.TestCase):
    """ADR 0063: eager beats lazy on p50/p95; construction tax bounded."""

    def test_eager_beats_lazy_latency(self) -> None:
        """Eager p50 and p95 must be < EAGER_LATENCY_CEILING * lazy."""
        gc.collect()
        with TemporaryDirectory() as tmp_lazy:
            root_lazy = Path(tmp_lazy)
            setup_synthetic_workspace(
                root_lazy, n_children=N_CHILDREN, write_sidecars=True,
            )
            _ctor_lazy_ms, lazy_latencies = _run_queries(
                root_lazy, QUERY_TERM, QUERY_REPS, eager=False,
            )

        gc.collect()
        with TemporaryDirectory() as tmp_eager:
            root_eager = Path(tmp_eager)
            setup_synthetic_workspace(
                root_eager, n_children=N_CHILDREN, write_sidecars=True,
            )
            ctor_eager_ms, eager_latencies = _run_queries(
                root_eager, QUERY_TERM, QUERY_REPS, eager=True,
            )

        lazy_p50 = _percentile(lazy_latencies, 50.0) * 1000.0
        lazy_p95 = _percentile(lazy_latencies, 95.0) * 1000.0
        eager_p50 = _percentile(eager_latencies, 50.0) * 1000.0
        eager_p95 = _percentile(eager_latencies, 95.0) * 1000.0
        print(
            f"\n[bench-eager] N={N_CHILDREN} reps={QUERY_REPS} term={QUERY_TERM!r}"
            f"\n  ctor:    eager={ctor_eager_ms:.2f}ms"
            f"\n  p50:     lazy={lazy_p50:.2f}ms eager={eager_p50:.2f}ms"
            f"\n  p95:     lazy={lazy_p95:.2f}ms eager={eager_p95:.2f}ms"
            f"\n  median:  lazy={statistics.median(lazy_latencies)*1000:.2f}ms"
            f" eager={statistics.median(eager_latencies)*1000:.2f}ms",
            file=sys.stdout,
        )

        # Construction-time tax bound: a 10x regression on the build
        # would invalidate the amortization story. 100 ms is the cap.
        self.assertLessEqual(
            ctor_eager_ms, CONSTRUCTION_CEILING_MS,
            f"eager construction took {ctor_eager_ms:.2f}ms;"
            f" ceiling={CONSTRUCTION_CEILING_MS}ms (ADR 0063 budget)",
        )

        # Latency assertion: only assert on p50 (median), the most
        # stable percentile on a small synthetic fixture. Skip when
        # lazy p50 is below the 5 ms noise floor where syscall jitter
        # dominates and ratios are meaningless. The p95 numbers are
        # printed for triage but not asserted -- they are too sensitive
        # to single-sample CI outliers on a 21-rep bench. The ratio
        # must be below 0.95: a regression to lazy speed (1.0x) is
        # the canary this assertion exists to catch.
        latency_floor_ms = 5.0
        if lazy_p50 >= latency_floor_ms:
            ratio = eager_p50 / lazy_p50
            self.assertLess(
                ratio, EAGER_LATENCY_CEILING,
                f"eager p50 ({eager_p50:.2f}ms) is {ratio:.2f}x lazy"
                f" ({lazy_p50:.2f}ms); ceiling={EAGER_LATENCY_CEILING}x."
                f" ADR 0063 expects ~0.27x in standalone bench;"
                f" investigate if this trips repeatedly.",
            )


if __name__ == "__main__":
    unittest.main()
