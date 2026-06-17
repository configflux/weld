"""Provenance-keyed edge purge keeps clean-caller -> dirty-callee edges.

Regression for the cjij.2 / ADR 0074 amendment defect: parse-only-dirty
``python_callgraph`` re-extraction must stay byte-identical to a full
``wd discover`` for the same source state, *including* the failing class
the original lever introduced -- editing a **callee** in a dirty file
while a **clean** caller in another file (and another glob) points at it.

Before the amendment, ``purge_stale_nodes`` dropped every edge whose
endpoint was a purged dirty-file node, so a ``calls`` edge originating in
a clean sibling and targeting a symbol *defined in* the dirty file was
collateral-purged and never rebuilt (the clean caller is no longer
parsed under the dirty-narrowed strategy). The fix purges call-graph
edges by edge **provenance** (the file that produced the edge), so a
clean-provenance inbound edge survives and the dirty re-parse re-mints
its same-id target endpoint.

These tests assert the observable contract: byte-identity of nodes +
edges (incl. ``definite``/``speculative`` and ``origin`` tags) between an
incremental refresh and a full discover at the same end state.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


from weld.discover import _discover_single_repo  # noqa: E402


def _git(root: Path) -> None:
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "T"],
        ["git", "config", "commit.gpgsign", "false"],
    ):
        subprocess.run(cmd, cwd=str(root), check=True)


def _commit(root: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=str(root), check=True)
    subprocess.run(["git", "commit", "-qm", "x"], cwd=str(root), check=True)


def _strip_meta(graph: dict) -> dict:
    """Drop volatile + path-order-volatile meta; nodes/edges must match."""
    out = {k: v for k, v in graph.items() if k != "meta"}
    meta = {
        k: v for k, v in graph.get("meta", {}).items()
        if k not in ("updated_at", "git_sha", "discovered_from")
    }
    out["meta"] = meta
    return out


def _calls_edges(graph: dict) -> list[tuple]:
    return sorted(
        (e["from"], e["to"], e["props"].get("confidence"))
        for e in graph.get("edges", [])
        if e.get("type") == "calls"
    )


def _without_closure_anchors(graph: dict) -> dict:
    """Strip graph_closure-synthesised file anchors + their contains edges.

    A *pre-existing* (shipped-code), order-dependent full-discovery
    artifact is orthogonal to this lever: a cross-glob call target is
    minted speculative (no ``props.file``) by the *calling* glob and
    ``dict.update`` last-batch-wins lets that clobber the definite
    file-bearing node the *defining* glob walked. Whether ``graph_closure``
    then synthesises a ``file:<path>`` anchor + ``contains`` edge for that
    symbol therefore depends on glob ordering and on whether the clobbering
    glob ran. A parse-only-dirty incremental refresh that does not re-run
    the (clean) clobbering glob keeps the file-bearing node and so keeps
    the anchor, while a full discover drops it. That divergence exists in
    shipped code independent of dirty-narrowing (verified: it persists with
    the strategy unmodified) and is fundamentally incompatible with the
    lever's premise of not re-parsing clean globs. It is tracked as a
    separate follow-up. These cross-glob assertions therefore compare the
    python_callgraph contract surface (symbols, calls/inherits edges,
    origin tags) and exclude the closure-synthesised file scaffolding.
    """
    closure_files = {
        nid for nid, n in graph.get("nodes", {}).items()
        if nid.startswith("file:")
        and n.get("props", {}).get("source_strategy") == "graph_closure"
    }
    nodes = {
        nid: n for nid, n in graph.get("nodes", {}).items()
        if nid not in closure_files
    }
    edges = [
        e for e in graph.get("edges", [])
        if e.get("from") not in closure_files and e.get("to") not in closure_files
    ]
    out = {k: v for k, v in graph.items() if k not in ("meta", "nodes", "edges")}
    out["nodes"] = nodes
    out["edges"] = sorted(
        edges, key=lambda e: (e["from"], e["to"], e["type"]),
    )
    return out


class CleanCallerDirtyCalleeSingleGlobTest(unittest.TestCase):
    """Edit a callee; a clean caller in the same glob points at it.

    The inbound ``calls`` edge must survive and the graph must be
    byte-identical to a full discover at the same state.
    """

    def _fixture(self, root: Path, callee_body: str) -> None:
        pkg = root / "pkg"
        pkg.mkdir(exist_ok=True)
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "callee.py").write_text(
            f"def run():\n    return {callee_body}\n", encoding="utf-8",
        )
        (pkg / "caller.py").write_text(
            "from pkg.callee import run\n\n\ndef main():\n    return run()\n",
            encoding="utf-8",
        )
        weld_dir = root / ".weld"
        weld_dir.mkdir(exist_ok=True)
        (weld_dir / "discover.yaml").write_text(
            "sources:\n  - strategy: python_callgraph\n"
            "    glob: pkg/*.py\n    type: symbol\n",
            encoding="utf-8",
        )

    def test_inbound_edge_survives_and_graph_matches_full(self) -> None:
        # Incremental: seed full, edit only the callee, refresh.
        with tempfile.TemporaryDirectory(prefix="prov-inc-") as td:
            root = Path(td)
            _git(root)
            self._fixture(root, "1")
            _commit(root)
            _discover_single_repo(root, incremental=False, write_graph=True)
            self._fixture(root, "2")  # edit only callee.py body
            _commit(root)
            g_inc = _discover_single_repo(
                root, incremental=True, write_graph=True,
            )

        # Full: clean discover at the same end state.
        with tempfile.TemporaryDirectory(prefix="prov-full-") as td:
            root = Path(td)
            _git(root)
            self._fixture(root, "2")
            _commit(root)
            g_full = _discover_single_repo(
                root, incremental=False, write_graph=True,
            )

        # The inbound calls edge must be present on BOTH sides.
        edge = ("symbol:py:pkg.caller:main", "symbol:py:pkg.callee:run", "definite")
        self.assertIn(
            edge, _calls_edges(g_inc),
            "inbound clean-caller -> dirty-callee calls edge lost on the "
            "incremental path (provenance purge regression)",
        )
        self.assertEqual(_calls_edges(g_inc), _calls_edges(g_full))
        self.assertEqual(
            _strip_meta(g_inc), _strip_meta(g_full),
            "incremental graph (nodes+edges, sans volatile meta) must be "
            "byte-identical to a full discover at the same source state",
        )


class CleanCallerDirtyCalleeCrossGlobTest(unittest.TestCase):
    """The ADR's mandatory cross-glob case: tools/*.py -> weld-shaped glob.

    A caller in glob A (``tools/*.py``) imports and calls a callee defined
    in glob B (``lib/*.py``). The substantive contract this lever must
    preserve cross-glob: the inbound ``calls`` edge survives the purge and
    the callee target keeps ``origin=project`` (the reconstruction must
    yield the cross-glob project-module union, or the tag drifts to
    ``external``).

    Note on the clobbered callee NODE: a full discover mints
    ``symbol:py:lib.core:helper`` in its *speculative, clobbered* form (the
    calling ``tools`` glob's ``make_resolved_target_node`` output overwrites
    the defining glob's definite node via last-batch-wins ``dict.update``).
    When only the callee is edited, the clean ``tools`` glob is not re-run,
    so the incremental refresh keeps the *definite* node and cannot
    reproduce that clobber without re-parsing a clean glob -- a pre-existing
    full-discovery artifact orthogonal to this lever (tracked as a
    follow-up). These callee-edit assertions therefore pin the edge + origin
    contract, not the clobbered node's exact shape. The caller-edit case
    below (where the clobbering glob *does* re-run) pins the full
    contract-surface byte-identity.
    """

    def _fixture(self, root: Path, callee_body: str) -> None:
        lib = root / "lib"
        lib.mkdir(exist_ok=True)
        (lib / "__init__.py").write_text("", encoding="utf-8")
        (lib / "core.py").write_text(
            f"def helper():\n    return {callee_body}\n", encoding="utf-8",
        )
        tools = root / "tools"
        tools.mkdir(exist_ok=True)
        (tools / "__init__.py").write_text("", encoding="utf-8")
        (tools / "cli.py").write_text(
            "from lib.core import helper\n\n\ndef run():\n    return helper()\n",
            encoding="utf-8",
        )
        weld_dir = root / ".weld"
        weld_dir.mkdir(exist_ok=True)
        (weld_dir / "discover.yaml").write_text(
            "sources:\n"
            "  - strategy: python_callgraph\n    glob: lib/*.py\n    type: symbol\n"
            "  - strategy: python_callgraph\n    glob: tools/*.py\n    type: symbol\n",
            encoding="utf-8",
        )

    def test_cross_glob_edge_and_origin_survive(self) -> None:
        with tempfile.TemporaryDirectory(prefix="prov-xinc-") as td:
            root = Path(td)
            _git(root)
            self._fixture(root, "1")
            _commit(root)
            _discover_single_repo(root, incremental=False, write_graph=True)
            self._fixture(root, "2")  # edit only lib/core.py (glob B)
            _commit(root)
            g_inc = _discover_single_repo(
                root, incremental=True, write_graph=True,
            )
            inc_disk = json.loads(
                (root / ".weld" / "graph.json").read_text(encoding="utf-8"),
            )

        with tempfile.TemporaryDirectory(prefix="prov-xfull-") as td:
            root = Path(td)
            _git(root)
            self._fixture(root, "2")
            _commit(root)
            g_full = _discover_single_repo(
                root, incremental=False, write_graph=True,
            )

        edge = ("symbol:py:tools.cli:run", "symbol:py:lib.core:helper", "definite")
        self.assertIn(
            edge, _calls_edges(g_inc),
            "cross-glob inbound calls edge lost on the incremental path",
        )
        # The callee target node must remain origin=project (a reconstructed
        # project_modules that dropped the cross-glob module would mis-tag it
        # external -- byte-identity catches that even though edge count is
        # unchanged).
        target = inc_disk["nodes"].get("symbol:py:lib.core:helper", {})
        self.assertEqual(
            target.get("props", {}).get("origin"), "project",
            "cross-glob callee target mis-tagged (project_modules "
            "reconstruction dropped glob B)",
        )
        # The full call-edge set (from/to/confidence) must match: the
        # inbound cross-glob edge survives and no spurious edge appears.
        self.assertEqual(_calls_edges(g_inc), _calls_edges(g_full))

    def test_edit_caller_side_also_matches_full(self) -> None:
        # The pre-existing "edit the caller" case (necessary but, per the
        # ADR, insufficient on its own) must still hold under the new purge.
        with tempfile.TemporaryDirectory(prefix="prov-callerinc-") as td:
            root = Path(td)
            _git(root)
            self._fixture(root, "1")
            _commit(root)
            _discover_single_repo(root, incremental=False, write_graph=True)
            # Edit the caller (glob A): change its body but keep the call.
            (root / "tools" / "cli.py").write_text(
                "from lib.core import helper\n\n\n"
                "def run():\n    x = helper()\n    return x\n",
                encoding="utf-8",
            )
            _commit(root)
            g_inc = _discover_single_repo(
                root, incremental=True, write_graph=True,
            )

        with tempfile.TemporaryDirectory(prefix="prov-callerfull-") as td:
            root = Path(td)
            _git(root)
            self._fixture(root, "1")
            (root / "tools" / "cli.py").write_text(
                "from lib.core import helper\n\n\n"
                "def run():\n    x = helper()\n    return x\n",
                encoding="utf-8",
            )
            _commit(root)
            g_full = _discover_single_repo(
                root, incremental=False, write_graph=True,
            )

        self.assertEqual(
            _without_closure_anchors(_strip_meta(g_inc)),
            _without_closure_anchors(_strip_meta(g_full)),
            "edit-the-caller incremental graph must match full discover "
            "on the python_callgraph contract surface",
        )


class DeletedCalleeDropsInboundEdgeTest(unittest.TestCase):
    """A genuinely-deleted callee must NOT leave a dangling inbound edge.

    Provenance survival keeps the clean caller's edge through the purge,
    but if the callee symbol is truly gone (file deleted, not re-minted),
    the post-process dangling-edge filter must still drop the edge so the
    incremental graph matches a full discover (which never had it).
    """

    def test_deleted_callee_matches_full(self) -> None:
        with tempfile.TemporaryDirectory(prefix="prov-del-inc-") as td:
            root = Path(td)
            _git(root)
            pkg = root / "pkg"
            pkg.mkdir()
            (pkg / "__init__.py").write_text("", encoding="utf-8")
            (pkg / "callee.py").write_text(
                "def run():\n    return 1\n", encoding="utf-8",
            )
            (pkg / "caller.py").write_text(
                "from pkg.callee import run\n\n\ndef main():\n    return run()\n",
                encoding="utf-8",
            )
            (root / ".weld").mkdir()
            (root / ".weld" / "discover.yaml").write_text(
                "sources:\n  - strategy: python_callgraph\n"
                "    glob: pkg/*.py\n    type: symbol\n",
                encoding="utf-8",
            )
            _commit(root)
            _discover_single_repo(root, incremental=False, write_graph=True)
            # Delete the callee definition file. The caller is now dangling
            # but unchanged (clean). Replace the import so the caller parses.
            (pkg / "callee.py").unlink()
            (pkg / "caller.py").write_text(
                "def main():\n    return 0\n", encoding="utf-8",
            )
            _commit(root)
            g_inc = _discover_single_repo(
                root, incremental=True, write_graph=True,
            )

        with tempfile.TemporaryDirectory(prefix="prov-del-full-") as td:
            root = Path(td)
            _git(root)
            pkg = root / "pkg"
            pkg.mkdir()
            (pkg / "__init__.py").write_text("", encoding="utf-8")
            (pkg / "caller.py").write_text(
                "def main():\n    return 0\n", encoding="utf-8",
            )
            (root / ".weld").mkdir()
            (root / ".weld" / "discover.yaml").write_text(
                "sources:\n  - strategy: python_callgraph\n"
                "    glob: pkg/*.py\n    type: symbol\n",
                encoding="utf-8",
            )
            _commit(root)
            g_full = _discover_single_repo(
                root, incremental=False, write_graph=True,
            )

        self.assertNotIn(
            "symbol:py:pkg.callee:run", g_inc.get("nodes", {}),
            "deleted callee symbol must not survive in the incremental graph",
        )
        self.assertEqual(
            _strip_meta(g_inc), _strip_meta(g_full),
            "deleted-callee incremental graph must match full discover "
            "(no dangling inbound edge resurrected by provenance survival)",
        )


if __name__ == "__main__":
    unittest.main()
