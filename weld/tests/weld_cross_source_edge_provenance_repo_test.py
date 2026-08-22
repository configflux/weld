"""Zero-violations gate: this repo's own real graph, ADR 0074 sixth amendment.

The unit fixtures in ``weld_graph_edge_provenance_lint_test.py`` pin the
rule's branch logic cheaply and hermetically. They cannot, by construction,
prove the rule catches the *next* strategy that regresses ADR 0074's
provenance invariant -- only a real discovery of this repo's own
``.weld/discover.yaml`` plus its real ``weld/strategies/*.py`` output can.

This is that gate. It runs a full, non-incremental discovery of the host
repo (the same real strategies, the same real config every other test in
this suite exercises indirectly) and asserts the ``cross-source-edge-
provenance`` rule reports zero violations. bd whnwb's own investigation
found exactly one live violation family this way -- ``validator_targets``'s
``validates`` edges carried no ``props.provenance.file`` -- fixed in the
same change that added this gate (see ``weld_validator_targets_strategy_
test.py``). A regression here names the strategy and the missing stamp
directly in the assertion failure, the same message ``wd lint`` would print.

Reads the host repo's tree and ``.weld/discover.yaml``, neither of which
Bazel sees as an input -- ``external``, same reason and same shape as
``weld_bazel_loads_repo_test.py`` and the ``discover_yaml_*_coverage_test``
family. Unlike those, this one also *runs* discovery rather than only
resolving config against the file system, so it is the more expensive
member of the family (a full discover of this repo, single-digit seconds);
paid once per test-target invocation in ``setUpClass``, not per test case.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from weld._graph_edge_provenance_lint import check_cross_source_edge_provenance

_REPO_ROOT = Path(__file__).resolve().parents[2]


class RealRepoCrossSourceEdgeProvenanceTest(unittest.TestCase):
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

    def test_no_cross_source_edges_lack_provenance(self) -> None:
        nodes = self.graph.get("nodes", {})
        edges = self.graph.get("edges", [])
        # Guards the guard: an empty graph would make the assertion below
        # vacuously true.
        self.assertTrue(nodes, "real discovery produced no nodes")
        self.assertTrue(edges, "real discovery produced no edges")
        violations = list(
            check_cross_source_edge_provenance(_REPO_ROOT, nodes, edges)
        )
        messages = "\n".join(v.message for v in violations)
        self.assertEqual(
            [], violations,
            f"{len(violations)} cross-source edge(s) with no ADR 0074 "
            f"provenance stamp on the real graph:\n{messages}",
        )


if __name__ == "__main__":
    unittest.main()
