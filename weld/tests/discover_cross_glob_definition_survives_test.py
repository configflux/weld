"""A cross-glob call target must not erase the definition (bd 4ux4, ADR 0103).

This is a *full*-discover defect: no incremental path is involved. One source
entry walks ``lib/core.py`` and emits the real definition of ``helper``
(definite, with ``file``/``line``/``kind``); a later entry resolves
``tools/cli.py``'s call onto the same node ID and mints the evidence-free stub
``python_callgraph`` uses to keep the graph referentially closed. The
orchestrator's ``dict.update`` let the stub win, so ``graph_closure`` had no
``props.file`` to anchor on and derived no ``file:lib/core --contains-->``
edge -- a live, defined, public symbol with no file and no containment.

The sibling file ``incremental_callgraph_provenance_purge_test`` pins the same
fixture's incremental==full byte-identity. This one pins the full run's own
correctness and, in ``SourceOrderTest``, that the outcome no longer depends on
which entry the config happens to declare first.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from weld.discover import _discover_single_repo

CALLEE_ID = "symbol:py:lib.core:helper"
CALLER_ID = "symbol:py:tools.cli:run"
CALLEE_FILE_ID = "file:lib/core"

_DEFINING_SOURCE = "  - strategy: python_callgraph\n    glob: lib/*.py\n    type: symbol\n"
_CALLING_SOURCE = "  - strategy: python_callgraph\n    glob: tools/*.py\n    type: symbol\n"


def _git(root: Path) -> None:
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "T"],
        ["git", "config", "commit.gpgsign", "false"],
    ):
        subprocess.run(cmd, cwd=str(root), check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(root), check=True)
    subprocess.run(["git", "commit", "-qm", "x"], cwd=str(root), check=True)


def _fixture(root: Path, *, defining_first: bool = True) -> None:
    """Two globs: ``lib`` defines ``helper``, ``tools`` calls it."""
    lib = root / "lib"
    lib.mkdir(exist_ok=True)
    (lib / "__init__.py").write_text("", encoding="utf-8")
    (lib / "core.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    tools = root / "tools"
    tools.mkdir(exist_ok=True)
    (tools / "__init__.py").write_text("", encoding="utf-8")
    (tools / "cli.py").write_text(
        "from lib.core import helper\n\n\ndef run():\n    return helper()\n",
        encoding="utf-8",
    )
    order = (
        (_DEFINING_SOURCE, _CALLING_SOURCE) if defining_first
        else (_CALLING_SOURCE, _DEFINING_SOURCE)
    )
    weld_dir = root / ".weld"
    weld_dir.mkdir(exist_ok=True)
    (weld_dir / "discover.yaml").write_text(
        "sources:\n" + "".join(order), encoding="utf-8",
    )


def _full_discover(defining_first: bool = True) -> dict:
    with tempfile.TemporaryDirectory(prefix="xglob-") as td:
        root = Path(td)
        _fixture(root, defining_first=defining_first)
        _git(root)
        return _discover_single_repo(root, incremental=False, write_graph=True)


def _contains_edges(graph: dict, target: str) -> list[str]:
    return sorted(
        e["from"] for e in graph["edges"]
        if e["type"] == "contains" and e["to"] == target
    )


class DefinitionSurvivesTheCallTargetStubTest(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = _full_discover()
        self.props = self.graph["nodes"][CALLEE_ID]["props"]

    def test_callee_keeps_its_definition_evidence(self) -> None:
        # The exact four fields the stub used to erase.
        self.assertEqual(self.props.get("confidence"), "definite")
        self.assertEqual(self.props.get("file"), "lib/core.py")
        self.assertEqual(self.props.get("line"), 1)
        self.assertEqual(self.props.get("kind"), "function")

    def test_callee_is_contained_by_its_file(self) -> None:
        # graph_closure derives this from props.file, so it is the observable
        # the clobber took out (the single differing edge in the bd 4ux4 repro).
        self.assertIn(CALLEE_FILE_ID, self.graph["nodes"])
        self.assertEqual(_contains_edges(self.graph, CALLEE_ID), [CALLEE_FILE_ID])

    def test_vetoing_the_stub_leaves_no_dangling_edge(self) -> None:
        # The stub exists to keep every edge endpoint resolvable; rejecting it
        # is only safe because the definition already occupies that node ID.
        calls = [
            (e["from"], e["to"]) for e in self.graph["edges"] if e["type"] == "calls"
        ]
        self.assertIn((CALLER_ID, CALLEE_ID), calls)
        for src, dst in calls:
            self.assertIn(src, self.graph["nodes"])
            self.assertIn(dst, self.graph["nodes"])

    def test_origin_stays_project(self) -> None:
        # ADR 0042's reconciliation healed origin alone while the rest of the
        # node stayed clobbered; it must keep working now that it is a no-op.
        self.assertEqual(self.props.get("origin"), "project")


class SourceOrderTest(unittest.TestCase):
    """Declaring the calling entry first must produce the same graph.

    Before the fix the answer depended entirely on this ordering: the stub won
    when its entry ran last and lost when it ran first.
    """

    def test_graph_is_identical_under_reversed_source_order(self) -> None:
        forward = _full_discover(defining_first=True)
        reverse = _full_discover(defining_first=False)
        for graph in (forward, reverse):
            graph.pop("meta", None)
        self.assertEqual(forward["nodes"], reverse["nodes"])
        self.assertEqual(forward["edges"], reverse["edges"])


if __name__ == "__main__":
    unittest.main()
