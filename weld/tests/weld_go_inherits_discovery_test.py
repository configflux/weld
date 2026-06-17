"""End-to-end discovery test for the Go inheritance edge emitter (ADR 0064 criterion 2).

Complements the pure-helper unit suite in
:mod:`weld.tests.weld_go_inherits_test`: it runs the *real*
``weld.discover.discover`` over the bundled Go fixture
(``weld/tests/fixtures/tier1/go/sample_go``) and asserts the new edges
are emitted on a genuine graph -- the same path ``wd discover`` drives:

* Circle/Rectangle embed ``shapes.Base`` -> ``inherits`` edges,
* Circle/Rectangle define ``Area`` and promote ``Base.Describe`` via
  embedding, so their method set covers ``shapes.Shape`` ->
  ``implements`` edges,
* every inheritance edge originates at a ``symbol:go:`` node (criterion 2)
  and resolves to a real graph node (no orphan ``to:``).

Split into its own file (not folded into the unit suite) so each test
module stays a single cohesive responsibility within the line-count cap.
The class self-skips when the fixture is not reachable in the sandbox.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path


def _fixture_root() -> Path:
    """Locate the bundled Go fixture (in-repo, else Bazel runfiles)."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    rel = repo_root / "weld" / "tests" / "fixtures" / "tier1" / "go" / "sample_go"
    if rel.is_dir():
        return rel
    runfiles = os.environ.get("RUNFILES_DIR")
    if runfiles:
        rf = (Path(runfiles) / "_main" / "weld" / "tests" / "fixtures"
              / "tier1" / "go" / "sample_go")
        if rf.is_dir():
            return rf
    return rel


class GoFixtureDiscoveryTest(unittest.TestCase):
    """Discover the bundled Go fixture and assert the new edges."""

    def _discover_edges(self) -> tuple[set, set]:
        fixture = _fixture_root()
        if not fixture.is_dir():
            self.skipTest(f"go sample_go fixture not reachable at {fixture}")
        from weld.discover import discover

        with tempfile.TemporaryDirectory(prefix="go-inherits-it-") as tmp:
            corpus = Path(tmp) / "sample_go"
            shutil.copytree(fixture, corpus)
            graph = discover(corpus, incremental=False, with_sqlite=False)
            nodes = graph.get("nodes", {}) if isinstance(graph, dict) else graph.nodes
            edges = graph.get("edges", []) if isinstance(graph, dict) else graph.edges
            inherits = {
                (e["from"], e["to"]) for e in edges if e.get("type") == "inherits"
            }
            implements = {
                (e["from"], e["to"]) for e in edges if e.get("type") == "implements"
            }
            # every endpoint resolves to a real node (criterion 2: no orphan).
            for src, dst in inherits | implements:
                self.assertIn(src, nodes, f"orphan inherits/implements from {src}")
                self.assertIn(dst, nodes, f"orphan inherits/implements to {dst}")
            return inherits, implements

    def test_struct_embedding_emits_inherits(self) -> None:
        inherits, _ = self._discover_edges()
        self.assertIn(
            ("symbol:go:geometry.geometry:Circle", "symbol:go:shapes.shapes:Base"),
            inherits,
        )
        self.assertIn(
            ("symbol:go:geometry.geometry:Rectangle", "symbol:go:shapes.shapes:Base"),
            inherits,
        )

    def test_interface_satisfaction_emits_implements(self) -> None:
        _, implements = self._discover_edges()
        self.assertIn(
            ("symbol:go:geometry.geometry:Circle", "symbol:go:shapes.shapes:Shape"),
            implements,
        )
        self.assertIn(
            ("symbol:go:geometry.geometry:Rectangle", "symbol:go:shapes.shapes:Shape"),
            implements,
        )

    def test_inherits_implements_originate_at_symbol_nodes(self) -> None:
        # ADR 0064 criterion 2: no file-origin inheritance edges.
        inherits, implements = self._discover_edges()
        for src, _dst in inherits | implements:
            self.assertTrue(
                src.startswith("symbol:go:"),
                f"inheritance edge must originate at a symbol node, got {src}",
            )


if __name__ == "__main__":
    unittest.main()
