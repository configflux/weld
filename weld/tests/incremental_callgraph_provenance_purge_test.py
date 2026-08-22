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

    Both directions assert *whole-graph* byte-identity. They did not always:
    a full discover used to mint ``symbol:py:lib.core:helper`` in its
    speculative, clobbered form, because the calling ``tools`` glob's
    ``make_resolved_target_node`` output overwrote the defining glob's
    definite node under last-batch-wins ``dict.update``. An incremental
    refresh that never re-ran the clean ``tools`` glob kept the definite node
    -- and its ``graph_closure`` file anchor and ``contains`` edge -- so the
    two paths genuinely disagreed. This file used to filter that disagreement
    out through a ``_without_closure_anchors`` helper and compare only the
    python_callgraph contract surface. ADR 0103 fixed the clobber at the
    orchestrator merge (bd 4ux4), so the filter is gone and the exclusion it
    hid is now pinned.
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
        # ADR 0103: whole-graph, including the graph_closure file anchor and
        # ``contains`` edge the clobber used to take out of the full run.
        self.assertEqual(
            _strip_meta(g_inc), _strip_meta(g_full),
            "edit-the-callee incremental graph must be byte-identical to a "
            "full discover, closure-synthesised file scaffolding included",
        )

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
            _strip_meta(g_inc), _strip_meta(g_full),
            "edit-the-caller incremental graph must be byte-identical to a "
            "full discover, closure-synthesised file scaffolding included",
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
