"""Regression: incremental discovery must re-run strategies whose expected
outputs are missing from the existing ``graph.json``.

Symptom that motivated this regression:
After landing new source files, ``wd discover`` would write
``discovery-state.json`` containing those files (with their content hashes)
but *not* run the symbol-emitting strategy on them in that same run -- for
example because the prior run errored out partway, or because the state was
written by a code path that did not invoke the symbol strategy. On the next
incremental run, the dirty-set intersected with the source's
``source_file_map`` was empty (state already lists the files at their current
hashes), so the incremental path skipped the strategy and the resulting
``graph.json`` remained symbol-less. The user's only workaround was to delete
``discovery-state.json`` and force a full re-scan.

The fix audits the existing graph: for each source entry whose
``source_file_map`` is non-empty, if no node in the loaded graph has a
``props.file`` falling inside that source's file set, every file in that set
is treated as dirty so the strategy re-runs and the missing nodes land. This
test pins that invariant and adds an inverse test that proves the audit does
not cause a perf regression when state and graph are mutually consistent.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld import discover as discover_mod  # noqa: E402
from weld.discover import _discover_single_repo  # noqa: E402
from weld.discovery_state import build_file_hashes  # noqa: E402


def _build_fixture(root: Path) -> None:
    """Minimal fixture: one source file processed by ``python_module``.

    The strategy emits at least one node carrying ``props.file ==
    "src/mod.py"`` so the audit has something concrete to look for.
    """
    src = root / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "mod.py").write_text(
        "def helper():\n    return 1\n",
        encoding="utf-8",
    )

    (root / ".weld").mkdir()
    (root / ".weld" / "discover.yaml").write_text(
        "topology:\n"
        "  nodes:\n"
        "    - id: pkg:src\n"
        "      type: package\n"
        "      label: src\n"
        "sources:\n"
        "  - strategy: python_module\n"
        "    glob: src/**/*.py\n"
        "    type: file\n"
        "    package: pkg:src\n",
        encoding="utf-8",
    )


def _seed_state_and_graph(root: Path, *, omit_file_in_graph: str | None) -> None:
    """Seed ``discovery-state.json`` with the current file hashes and write a
    ``graph.json`` that *does not* contain any node for *omit_file_in_graph*.

    This simulates the "state is current but graph is missing strategy
    output for that file" condition the bug surfaced.
    """
    seed_graph = _discover_single_repo(root, incremental=False)

    if omit_file_in_graph is not None:
        kept_nodes = {
            nid: node
            for nid, node in seed_graph.get("nodes", {}).items()
            if node.get("props", {}).get("file", "") != omit_file_in_graph
        }
        kept_ids = set(kept_nodes.keys())
        kept_edges = [
            e for e in seed_graph.get("edges", [])
            if e["from"] in kept_ids and e["to"] in kept_ids
        ]
        seed_graph = dict(seed_graph)
        seed_graph["nodes"] = kept_nodes
        seed_graph["edges"] = kept_edges

    (root / ".weld" / "graph.json").write_text(
        json.dumps(seed_graph), encoding="utf-8",
    )

    state_path = root / ".weld" / "discovery-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    on_disk = build_file_hashes(root, list(state["files"].keys()))
    assert state["files"] == on_disk, (
        "fixture invariant broken: state hashes diverge from on-disk hashes"
    )


class DiscoverIncrementalMissingOutputsTest(unittest.TestCase):
    """Pin the "graph is missing strategy outputs" -> force re-run invariant."""

    def test_incremental_reruns_strategy_when_graph_lacks_expected_nodes(
        self,
    ) -> None:
        """Symbols for a file must reappear when state lists the file but
        graph contains zero nodes for it.

        Without the fix: incremental returns the symbol-less graph because
        ``state_diff.dirty`` is empty.
        With the fix: the audit detects the missing outputs, marks the file
        dirty, the strategy re-runs, and the nodes land.
        """
        with tempfile.TemporaryDirectory(prefix="inc-missing-out-") as td:
            root = Path(td)
            _build_fixture(root)

            _seed_state_and_graph(root, omit_file_in_graph="src/mod.py")

            pre = json.loads(
                (root / ".weld" / "graph.json").read_text(encoding="utf-8")
            )
            pre_for_file = [
                nid for nid, n in pre.get("nodes", {}).items()
                if n.get("props", {}).get("file", "") == "src/mod.py"
            ]
            self.assertEqual(
                pre_for_file, [],
                "fixture invariant: seeded graph must lack nodes for src/mod.py",
            )

            graph = _discover_single_repo(root, incremental=True)

            post_for_file = [
                nid for nid, n in graph.get("nodes", {}).items()
                if n.get("props", {}).get("file", "") == "src/mod.py"
            ]
            self.assertGreater(
                len(post_for_file), 0,
                "incremental discovery must re-run the strategy when the "
                "existing graph is missing every node it would have produced "
                "for a tracked source file (correctness: graph completeness "
                "trumps incremental speed)",
            )

    def test_incremental_skips_strategy_when_graph_and_state_are_consistent(
        self,
    ) -> None:
        """Inverse: do not regress the no-changes fast path.

        When the existing graph already contains nodes for every tracked
        source file and no file content has changed, the incremental path
        must still skip strategy invocation entirely. We assert this by
        spying on ``_run_source`` -- it must not be called.
        """
        with tempfile.TemporaryDirectory(prefix="inc-noop-") as td:
            root = Path(td)
            _build_fixture(root)

            _seed_state_and_graph(root, omit_file_in_graph=None)

            with mock.patch.object(
                discover_mod, "_run_source",
                wraps=discover_mod._run_source,
            ) as spy:
                _discover_single_repo(root, incremental=True)

            self.assertEqual(
                spy.call_count, 0,
                "no-changes fast path regressed: the incremental "
                "discovery path invoked a strategy even though the "
                "existing graph already covers every tracked source file "
                "and no content has changed",
            )


if __name__ == "__main__":
    unittest.main()
