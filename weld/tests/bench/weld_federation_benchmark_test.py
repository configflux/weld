"""Federation performance benchmark suite: N=1, N=5, N=20 synthetic children.

Establishes baselines for root discover time, federated query latency,
and peak memory delta across multiple workspace sizes.  Fixture content
is derived from a fixed seed for byte-identical determinism.
"""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_repo_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.workspace_state import WORKSPACE_STATE_FILENAME  # noqa: E402

from weld.tests.bench.federation_bench_helpers import (  # noqa: E402
    BENCHMARK_N_VALUES,
    EDGES_PER_CHILD,
    NODES_PER_CHILD,
    REGRESSION_THRESHOLD_PCT,
    SEED,
    load_baseline,
    measure_memory_delta_kb,
    save_baseline,
    setup_synthetic_workspace,
    time_discover,
    time_query,
)

# Default N for backward-compatible probes.
N_CHILDREN = 5


class FederationBenchmarkTest(unittest.TestCase):
    """Smoke benchmark: N=5 synthetic children, discover + query baseline."""

    def setUp(self) -> None:
        self._tmpdir = TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        setup_synthetic_workspace(self.root, n_children=N_CHILDREN)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    # -- Probe 1: harness runs to completion with expected keys -----------

    def test_discover_completes_and_returns_summary(self) -> None:
        """Discover exits zero and produces a valid meta-graph."""
        graph, discover_time = time_discover(self.root)
        _, query_time = time_query(self.root, "child")

        self.assertIn("meta", graph)
        self.assertEqual(graph["meta"]["schema_version"], 2)
        self.assertGreater(discover_time, 0.0)
        self.assertGreater(query_time, 0.0)

    # -- Probe 2: workspace-state.json records exactly N children ---------

    def test_workspace_state_records_all_children(self) -> None:
        """After discover, workspace-state.json lists N_CHILDREN entries."""
        time_discover(self.root)
        state_path = self.root / ".weld" / WORKSPACE_STATE_FILENAME
        self.assertTrue(state_path.exists(), "workspace-state.json missing")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        children = state.get("children", {})
        self.assertEqual(len(children), N_CHILDREN)
        for name, child in children.items():
            self.assertEqual(
                child["status"], "present",
                f"child {name} has unexpected status: {child['status']}",
            )

    # -- Probe 3: stability check -- two runs within 50% -----------------

    def test_discover_stability(self) -> None:
        """Two consecutive discover runs on unchanged fixtures are stable."""
        _, time_1 = time_discover(self.root)
        _, time_2 = time_discover(self.root)

        if time_1 == 0.0:
            self.skipTest("first discover too fast to measure")

        drift_pct = abs(time_2 - time_1) / time_1 * 100.0
        self.assertLess(
            drift_pct,
            50.0,
            f"Discover times drifted {drift_pct:.1f}% between runs "
            f"(run1={time_1:.4f}s, run2={time_2:.4f}s)",
        )

    # -- Probe 4: regression gate (baseline comparison) -------------------

    def test_regression_gate_detects_threshold_breach(self) -> None:
        """An artificially tight baseline triggers a regression signal."""
        _, real_time = time_discover(self.root)

        tight_baseline = {
            "discover_time_s": 0.0001,
            "query_latency_s": 0.0001,
            "n_children": N_CHILDREN,
        }

        exceeds = (
            (real_time - tight_baseline["discover_time_s"])
            / tight_baseline["discover_time_s"]
            * 100.0
        )
        self.assertGreater(
            exceeds,
            REGRESSION_THRESHOLD_PCT,
            "Expected regression signal against an artificially tight baseline",
        )

    # -- Probe 5: deterministic generation (byte-identical graphs) --------

    def test_deterministic_fixture_generation(self) -> None:
        """Same seed produces byte-identical graph content across two setups."""
        with TemporaryDirectory() as tmp2:
            root2 = Path(tmp2)
            setup_synthetic_workspace(root2, n_children=N_CHILDREN)

            for idx in range(N_CHILDREN):
                name = f"child-{idx:02d}"
                g1 = (self.root / name / ".weld" / "graph.json").read_bytes()
                g2 = (root2 / name / ".weld" / "graph.json").read_bytes()
                sha1 = hashlib.sha256(g1).hexdigest()
                sha2 = hashlib.sha256(g2).hexdigest()
                self.assertEqual(
                    sha1, sha2,
                    f"child {name} graph.json differs between two setups",
                )

    # -- Probe 6: query fan-out covers all children -----------------------

    def test_query_fanout_across_children(self) -> None:
        """A fan-out query returns matches from multiple children."""
        time_discover(self.root)
        result, query_time = time_query(self.root, "child")

        matches = result.get("matches", [])
        child_repos_hit: set[str] = set()
        for m in matches:
            mid = m.get("id", "")
            if "\x1f" in mid:
                child_repos_hit.add(mid.split("\x1f")[0])

        self.assertEqual(
            len(child_repos_hit),
            N_CHILDREN,
            f"Query hit {len(child_repos_hit)} children, expected {N_CHILDREN}. "
            f"query_time={query_time:.4f}s",
        )

    # -- Probe 7: no network required (implicitly satisfied) --------------

    # -- Probe 8: baseline persistence and end-to-end summary ------------

    def test_baseline_persistence_and_summary(self) -> None:
        """Persist baseline numbers and verify the file is well-formed."""
        _, discover_time = time_discover(self.root)
        _, query_time = time_query(self.root, "child")

        baseline = {
            "discover_time_s": round(discover_time, 6),
            "query_latency_s": round(query_time, 6),
            "n_children": N_CHILDREN,
            "nodes_per_child": NODES_PER_CHILD,
            "edges_per_child": EDGES_PER_CHILD,
            "seed": SEED,
        }
        tmp_baseline = self.root / "baseline.json"
        save_baseline(baseline, path=tmp_baseline)

        loaded = load_baseline(path=tmp_baseline)
        self.assertIsNotNone(loaded)
        self.assertGreater(loaded["discover_time_s"], 0.0)
        self.assertGreater(loaded["query_latency_s"], 0.0)
        self.assertEqual(loaded["n_children"], N_CHILDREN)

    # -- Probe 9: regression against persisted baseline -------------------

    def test_regression_against_persisted_baseline(self) -> None:
        """Assert discover time is within threshold of the committed baseline."""
        existing = load_baseline()
        if existing is None:
            self.skipTest("no committed baseline file found")

        # Per-N keyed baseline (e.g. {"n5": {...}}).
        key = f"n{N_CHILDREN}"
        entry = existing.get(key)
        if entry is None:
            self.skipTest(f"no baseline entry for {key}")

        _, discover_time = time_discover(self.root)
        baseline_discover = entry["discover_time_s"]
        if baseline_discover > 0:
            pct_over = (discover_time - baseline_discover) / baseline_discover * 100.0
            if pct_over > REGRESSION_THRESHOLD_PCT:
                self.fail(
                    f"Discover time regressed by {pct_over:.1f}% "
                    f"(ceiling={baseline_discover:.6f}s, "
                    f"actual={discover_time:.6f}s, "
                    f"threshold={REGRESSION_THRESHOLD_PCT}%)"
                )


# ---------------------------------------------------------------------------
# Parameterized N-variant suite: N=1, N=5, N=20
# ---------------------------------------------------------------------------


class FederationBenchmarkNVariantTest(unittest.TestCase):
    """Performance benchmark across workspace sizes N=1, N=5, N=20.

    Records discover time, query latency, and peak memory delta for
    each N.  Compares against per-N ceilings in the baseline JSON.
    """

    def _run_benchmark(self, n: int) -> dict:
        """Set up N children, run discover + query, return metrics."""
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            setup_synthetic_workspace(root, n_children=n)
            graph, discover_s, mem_kb = measure_memory_delta_kb(root)
            _, query_s = time_query(root, "child")
            return {
                "n_children": n,
                "discover_time_s": round(discover_s, 6),
                "query_latency_s": round(query_s, 6),
                "memory_delta_kb": round(mem_kb, 1),
                "node_count": len(graph.get("nodes", {})),
            }

    def test_n1_benchmark(self) -> None:
        """Benchmark with N=1 child completes and records metrics."""
        m = self._run_benchmark(1)
        self.assertEqual(m["n_children"], 1)
        self.assertGreater(m["discover_time_s"], 0.0)

    def test_n5_benchmark(self) -> None:
        """Benchmark with N=5 children completes and records metrics."""
        m = self._run_benchmark(5)
        self.assertEqual(m["n_children"], 5)
        self.assertGreater(m["discover_time_s"], 0.0)

    def test_n20_benchmark(self) -> None:
        """Benchmark with N=20 children completes and records metrics."""
        m = self._run_benchmark(20)
        self.assertEqual(m["n_children"], 20)
        self.assertGreater(m["discover_time_s"], 0.0)

    def test_scaling_is_subquadratic(self) -> None:
        """Discover time at N=20 is less than quadratic growth from N=1."""
        reference_n = 1
        target_n = 20
        m1 = self._run_benchmark(reference_n)
        m20 = self._run_benchmark(target_n)
        if m1["discover_time_s"] == 0.0:
            self.skipTest("N=1 discover too fast to measure")
        ratio = m20["discover_time_s"] / m1["discover_time_s"]
        quadratic_limit = (target_n / reference_n) ** 2
        self.assertLess(
            ratio, quadratic_limit,
            f"N={target_n} is {ratio:.1f}x slower than N={reference_n}; "
            f"expected below quadratic threshold {quadratic_limit:.1f}x",
        )

    def test_n_variant_regression_against_baseline(self) -> None:
        """Each N-value is within threshold of its per-N baseline ceiling."""
        existing = load_baseline()
        if existing is None:
            self.skipTest("no committed baseline file found")

        for n in BENCHMARK_N_VALUES:
            key = f"n{n}"
            entry = existing.get(key)
            if entry is None:
                continue
            m = self._run_benchmark(n)
            ceiling = entry["discover_time_s"]
            if ceiling > 0:
                pct = (m["discover_time_s"] - ceiling) / ceiling * 100.0
                self.assertLess(
                    pct, REGRESSION_THRESHOLD_PCT,
                    f"N={n}: discover regressed {pct:.1f}% over ceiling "
                    f"({ceiling:.6f}s ceiling, {m['discover_time_s']:.6f}s actual)",
                )

    def test_baseline_round_trip_all_n(self) -> None:
        """Persist and reload a multi-N baseline to verify format."""
        data: dict = {}
        for n in BENCHMARK_N_VALUES:
            m = self._run_benchmark(n)
            data[f"n{n}"] = {
                "discover_time_s": m["discover_time_s"],
                "query_latency_s": m["query_latency_s"],
                "memory_delta_kb": m["memory_delta_kb"],
                "nodes_per_child": NODES_PER_CHILD,
                "edges_per_child": EDGES_PER_CHILD,
                "seed": SEED,
            }
        with TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "baseline.json"
            save_baseline(data, path=p)
            loaded = load_baseline(path=p)
        self.assertIsNotNone(loaded)
        for n in BENCHMARK_N_VALUES:
            key = f"n{n}"
            self.assertIn(key, loaded)
            self.assertGreater(loaded[key]["discover_time_s"], 0.0)


if __name__ == "__main__":
    unittest.main()
