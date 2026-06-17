"""ADR 0058 federation memory-peak probe (sqlite vs JSON read path).

Split from :mod:`weld.tests.bench.weld_federation_benchmark_test` to
keep the benchmark file under the project line-count cap. Probes the
*behavioural* invariants that make the memory-peak benefit possible:

- ``_load_child`` returns a :class:`SqliteBackedGraph` for every child
  when sidecars are present (no JSON Graph in memory).
- ``context`` over many children with sidecars never populates the JSON
  cache (``len(child_cache) == 0``).
- RSS delta over a context fan-out across N=20 children with sidecars
  does not regress catastrophically against the JSON-only baseline. CI
  RSS measurements are noisy so we give generous slack (>50% regression
  fails) rather than gating on an absolute ceiling.
"""

from __future__ import annotations

import resource
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


from weld._sqlite_reader import SqliteBackedGraph  # noqa: E402
from weld.federation import FederatedGraph  # noqa: E402

from weld.tests.bench.federation_bench_helpers import (  # noqa: E402
    setup_synthetic_workspace,
)


class FederationSqliteSidecarMemoryProbeTest(unittest.TestCase):
    """ADR 0058: sqlite-backed children avoid JSON parse on read paths."""

    def _fan_out_contexts(self, root: Path) -> tuple[FederatedGraph, list[str]]:
        """Run a context() call on the first node of every child.

        Returns the FederatedGraph instance and the list of canonical
        IDs used. Caller can introspect cache state on the instance.
        """
        fg = FederatedGraph(root)
        ids: list[str] = []
        for name in sorted(fg._children):
            child = fg._load_child(name)
            if isinstance(child, SqliteBackedGraph):
                node_iter = iter(child.iter_nodes())
            else:
                # JSON-backed fallback path -- traverse via dump.
                node_iter = iter(child.list_nodes())  # type: ignore[union-attr]
            first = next(node_iter, None)
            if first is None:
                continue
            cid = f"{name}\x1f{first['id']}"
            fg.context(cid, fallback=False)
            ids.append(cid)
        return fg, ids

    def test_sidecar_children_load_via_sqlite(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            setup_synthetic_workspace(root, n_children=5, write_sidecars=True)
            fg = FederatedGraph(root)
            try:
                for name in sorted(fg._children):
                    self.assertIsInstance(
                        fg._load_child(name),
                        SqliteBackedGraph,
                        f"child {name} should load via sqlite when sidecar fresh",
                    )
            finally:
                fg.close()

    def test_context_fan_out_does_not_populate_json_cache(self) -> None:
        """Sqlite path keeps the JSON ``child_cache`` empty.

        ``Graph`` parses fill the JSON cache; sqlite handles do not.
        This is the proxy for the memory-peak win: with sidecars, a
        full context fan-out across 20 children never builds an
        in-memory ``Graph`` for any of them.
        """
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            setup_synthetic_workspace(root, n_children=20, write_sidecars=True)
            fg, _ids = self._fan_out_contexts(root)
            try:
                self.assertEqual(
                    len(fg._child_cache), 0,
                    "context() over sqlite-backed children should not"
                    " populate the JSON child cache",
                )
            finally:
                fg.close()

    def test_sidecar_does_not_increase_rss_vs_json(self) -> None:
        """RSS over context fan-out: sidecar path should not regress.

        Compares peak RSS delta between two equivalent workspaces: one
        with sidecars (sqlite path), one without (JSON path). Asserts
        the sidecar path does not regress more than 50% over the JSON
        baseline. CI machines are noisy, so we use a generous ceiling --
        the sqlite path is expected to be at least comparable or better.
        """
        n_children = 20

        with TemporaryDirectory() as tmpdir_json:
            root_json = Path(tmpdir_json)
            setup_synthetic_workspace(
                root_json, n_children=n_children, write_sidecars=False,
            )
            before_json = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            fg_json, _ = self._fan_out_contexts(root_json)
            fg_json.close()
            after_json = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            json_delta = max(0.0, float(after_json - before_json))

        with TemporaryDirectory() as tmpdir_sqlite:
            root_sqlite = Path(tmpdir_sqlite)
            setup_synthetic_workspace(
                root_sqlite, n_children=n_children, write_sidecars=True,
            )
            before_sqlite = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            fg_sqlite, _ = self._fan_out_contexts(root_sqlite)
            fg_sqlite.close()
            after_sqlite = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            sqlite_delta = max(0.0, float(after_sqlite - before_sqlite))

        # Synthetic graphs are tiny; both deltas can be 0 (no peak
        # change). Skip the comparison when the JSON baseline is too
        # small to be meaningful -- the behavioural test above already
        # covers the invariant.
        if json_delta < 1024.0:
            self.skipTest(
                f"JSON RSS delta too small to measure"
                f" (json={json_delta}KB, sqlite={sqlite_delta}KB)"
            )
        ratio = sqlite_delta / json_delta if json_delta > 0 else 0.0
        self.assertLess(
            ratio, 1.5,
            f"sqlite-backed federation regressed RSS over JSON by"
            f" {ratio:.2f}x (json={json_delta}KB, sqlite={sqlite_delta}KB)",
        )


if __name__ == "__main__":
    unittest.main()
