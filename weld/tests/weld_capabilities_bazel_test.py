"""Capability-matrix tests for the Bazel framework (ADR 0044, Layer C1).

Pulled into its own file to keep ``weld_capabilities_test.py`` under the
400-line source cap. Shares the ``_make_repo`` fixture pattern with the
parent module but does not import from it (test files cannot have a
runtime cross-import in this repo).
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.capabilities import compute_capabilities  # noqa: E402
from weld.contract import SCHEMA_VERSION  # noqa: E402


def _make_repo(
    nodes: dict[str, dict] | None,
    *,
    yaml_strategies: list[str] | None,
) -> Path:
    root = Path(tempfile.mkdtemp())
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    (root / ".weld" / "graph.json").write_text(
        json.dumps(
            {
                "meta": {
                    "version": SCHEMA_VERSION,
                    "updated_at": "2026-05-03T00:00:00+00:00",
                },
                "nodes": nodes or {},
                "edges": [],
            },
        ),
        encoding="utf-8",
    )
    if yaml_strategies is not None:
        sources = "\n".join(
            f"  - glob: '*'\n    type: file\n    strategy: {s}"
            for s in yaml_strategies
        )
        (root / ".weld" / "discover.yaml").write_text(
            f"sources:\n{sources}\n", encoding="utf-8",
        )
    return root


class BazelFrameworkCapabilityTest(unittest.TestCase):
    """Externally-visible check that the registry update for ADR 0044 landed.

    Does not introspect the bazel strategy implementation -- only the
    capability matrix. If the registry forgets to claim ``srcs_edges``
    or ``deps_edges`` evidence for the bazel framework, this test fails.
    """

    def test_srcs_and_deps_edges_evidence_when_wired(self) -> None:
        nodes = {
            "file:weld/BUILD.bazel": {
                "type": "file",
                "props": {"file": "weld/BUILD.bazel"},
            },
            "build-target://weld:lib": {
                "type": "build-target",
                "props": {
                    "file": "weld/BUILD.bazel",
                    "source_strategy": "bazel",
                },
            },
        }
        root = _make_repo(nodes, yaml_strategies=["bazel"])
        graph_data = json.loads(
            (root / ".weld" / "graph.json").read_text(encoding="utf-8"),
        )
        result = compute_capabilities(graph_data, root)
        self.assertIn("bazel", result["frameworks"])
        bazel = result["frameworks"]["bazel"]
        self.assertTrue(bazel["nodes_emitted"])
        self.assertTrue(bazel["srcs_edges"])
        self.assertTrue(bazel["deps_edges"])

    def test_srcs_and_deps_edges_false_when_strategy_unwired(self) -> None:
        """Even with BUILD nodes, evidence is False if bazel is not wired."""
        nodes = {
            "file:weld/BUILD.bazel": {
                "type": "file",
                "props": {"file": "weld/BUILD.bazel"},
            },
        }
        root = _make_repo(nodes, yaml_strategies=["python_module"])
        graph_data = json.loads(
            (root / ".weld" / "graph.json").read_text(encoding="utf-8"),
        )
        result = compute_capabilities(graph_data, root)
        self.assertIn("bazel", result["frameworks"])
        bazel = result["frameworks"]["bazel"]
        self.assertFalse(bazel["nodes_emitted"])
        self.assertFalse(bazel["srcs_edges"])
        self.assertFalse(bazel["deps_edges"])


if __name__ == "__main__":
    unittest.main()
