"""Zero-violations gate: this repo's own real graph satisfies its own contract.

The unit fixtures in ``weld_graph_contract_check_test.py`` pin
``weld._graph_contract_check``'s own branch logic cheaply and hermetically.
They cannot, by construction, prove the checker catches the *next* strategy
that regresses ``weld.contract`` -- only a real discovery of this repo's own
``.weld/discover.yaml`` plus its real ``weld/strategies/*.py`` output can.
Same shape and same reason as ``weld_cross_source_edge_provenance_repo_test.py``
(ADR 0074 sixth amendment, bd whnwb); this is the generalized bd rhuc
counterpart for node/edge contract conformance rather than edge provenance.

This is that gate. It runs a full, non-incremental discovery of the host
repo and asserts every emitted node and edge passes
``weld.contract.validate_node`` / ``validate_edge``. bd rgru's own
investigation found exactly one live violation family this way --
``python_package`` (and, by the same defect, ``csharp_package``) stamped
``roles: ["package"]`` while ``"package"`` was absent from ``ROLE_VALUES``
-- fixed by adding the role to the contract vocabulary. A regression here
names the offending strategy and field directly in the assertion failure.

Reads the host repo's tree and ``.weld/discover.yaml``, neither of which
Bazel sees as an input -- ``external``, same reason and shape as
``weld_cross_source_edge_provenance_repo_test.py`` and
``weld_bazel_loads_repo_test.py``. Also *runs* discovery rather than only
resolving config against the file system, so it is one of the more
expensive members of that family (a full discover of this repo,
single-digit seconds); paid once per test-target invocation in
``setUpClass``, not per test case.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from weld._graph_contract_check import check_node_edge_contract

_REPO_ROOT = Path(__file__).resolve().parents[2]


class RealRepoNodeEdgeContractTest(unittest.TestCase):
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

    def test_every_node_and_edge_satisfies_the_contract(self) -> None:
        nodes = self.graph.get("nodes", {})
        edges = self.graph.get("edges", [])
        # Guards the guard: an empty graph would make the assertion below
        # vacuously true.
        self.assertTrue(nodes, "real discovery produced no nodes")
        self.assertTrue(edges, "real discovery produced no edges")
        violations = list(check_node_edge_contract(nodes, edges))
        messages = "\n".join(str(v) for v in violations)
        self.assertEqual(
            [], violations,
            f"{len(violations)} node/edge(s) on the real graph violate the "
            f"weld.contract node/edge contract:\n{messages}",
        )


if __name__ == "__main__":
    unittest.main()
