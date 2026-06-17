"""Incremental refresh is byte-equivalent to a full discover (bd 85tb.2).

Covers the pieces the sub-second incremental refresh added on top of the
existing incremental path:

* ``dumps_graph_canonical`` emits byte-identical output to ``dumps_graph``
  for an already-canonical graph and falls back safely otherwise.
* ``files_missing_strategy_outputs`` exempts files the prior run proved
  node-less, so a node-less source stops perpetually re-triggering.
* ``_discover_single_repo`` with ``write_graph=True`` writes graph.json +
  sidecars whose node/edge content matches a full discover, and the
  query-state sidecar is a cold-load hit against the on-disk graph.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


from weld import _query_sidecar as sidecar  # noqa: E402
from weld.discover import _discover_single_repo  # noqa: E402
from weld.discovery_state import files_missing_strategy_outputs  # noqa: E402
from weld.graph_closure import _module_index  # noqa: E402
from weld.serializer import dumps_graph, dumps_graph_canonical  # noqa: E402


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


def _fixture(root: Path) -> None:
    src = root / "src"
    src.mkdir()
    (src / "__init__.py").write_text("", encoding="utf-8")
    for n in ("alpha", "beta", "gamma"):
        (src / f"{n}.py").write_text(f"def {n}_fn():\n    return 1\n", encoding="utf-8")
    (root / ".weld").mkdir()
    (root / ".weld" / "discover.yaml").write_text(
        "topology:\n  nodes:\n    - id: pkg:src\n      type: package\n"
        "      label: src\nsources:\n  - strategy: python_module\n"
        "    glob: src/**/*.py\n    type: file\n    package: pkg:src\n",
        encoding="utf-8",
    )


def _strip_meta_graph(graph: dict) -> dict:
    """Return graph with volatile + path-order-volatile meta removed.

    ``discovered_from`` legitimately differs between incremental and full
    runs (incremental accumulates the changed file path); nodes and edges
    must match exactly.
    """
    out = {k: v for k, v in graph.items() if k != "meta"}
    meta = {
        k: v for k, v in graph.get("meta", {}).items()
        if k not in ("updated_at", "git_sha", "discovered_from")
    }
    out["meta"] = meta
    return out


class DumpsGraphCanonicalTest(unittest.TestCase):
    def test_matches_dumps_graph_for_canonical_input(self) -> None:
        graph = {
            "meta": {"version": 5},
            "nodes": {"b:2": {"type": "x", "label": "b", "props": {}},
                      "a:1": {"type": "x", "label": "a", "props": {}}},
            "edges": [
                {"from": "a:1", "to": "b:2", "type": "calls",
                 "props": {"confidence": "definite"}},
            ],
        }
        # canonical_graph sorts edges; build a canonical form first.
        from weld.serializer import canonical_graph
        canon = canonical_graph(graph)
        self.assertEqual(dumps_graph_canonical(canon), dumps_graph(canon))

    def test_falls_back_when_edges_unsorted(self) -> None:
        # Edges deliberately out of canonical order -> fast path must
        # transparently fall back to the full canonicalizer and still emit
        # the canonical (sorted) bytes.
        graph = {
            "meta": {"version": 5},
            "nodes": {"a:1": {"type": "x", "label": "a", "props": {}},
                      "b:2": {"type": "x", "label": "b", "props": {}}},
            "edges": [
                {"from": "b:2", "to": "a:1", "type": "calls",
                 "props": {"confidence": "definite"}},
                {"from": "a:1", "to": "b:2", "type": "calls",
                 "props": {"confidence": "definite"}},
            ],
        }
        self.assertEqual(dumps_graph_canonical(graph), dumps_graph(graph))


class FilesMissingExemptTest(unittest.TestCase):
    def test_exempt_file_not_flagged(self) -> None:
        # A source whose strategy emits abstract concepts anchors to no file,
        # so its file never appears in files_with_nodes.
        graph = {"nodes": {"concept:x": {"type": "concept", "label": "x", "props": {}}}}
        source_map = [["issues.jsonl"]]
        # Without exemption the node-less source is flagged.
        self.assertEqual(
            files_missing_strategy_outputs(graph, source_map),
            {"issues.jsonl"},
        )
        # With it recorded as legitimately node-less, it is skipped.
        self.assertEqual(
            files_missing_strategy_outputs(graph, source_map, {"issues.jsonl"}),
            set(),
        )

    def test_partial_exempt_still_flags_unexempt_members(self) -> None:
        # A source with two node-less files where only one is exempt must
        # still re-trigger (the set is not fully covered).
        graph = {"nodes": {}}
        source_map = [["a.txt", "b.txt"]]
        self.assertEqual(
            files_missing_strategy_outputs(graph, source_map, {"a.txt"}),
            {"a.txt", "b.txt"},
        )


class IncrementalDiscoverEquivalenceTest(unittest.TestCase):
    def test_incremental_nodes_edges_match_full_and_sidecar_hits(self) -> None:
        # Incremental (seed full -> change one file -> incremental) vs a
        # clean full discover at the same end state. Nodes/edges must match
        # byte-for-byte; graph.json (volatile-stripped) cold-load must hit
        # the query-state sidecar.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            _fixture(root)
            _commit(root)
            _discover_single_repo(root, incremental=False, write_graph=True)
            (root / "src" / "alpha.py").write_text(
                "def alpha_fn():\n    return 99\nX = 5\n", encoding="utf-8",
            )
            _commit(root)
            g_inc = _discover_single_repo(root, incremental=True, write_graph=True)
            on_disk = json.loads(
                (root / ".weld" / "graph.json").read_text(encoding="utf-8"),
            )
            # Cold-load sidecar hit against on-disk (stripped) graph.
            hit = sidecar.read_sidecar(
                root / ".weld" / "graph.json",
                on_disk.get("nodes", {}), on_disk.get("edges", []),
            )
            self.assertIsNotNone(
                hit, "query-state sidecar must be a cold-load hit after refresh",
            )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            _fixture(root)
            (root / "src" / "alpha.py").write_text(
                "def alpha_fn():\n    return 99\nX = 5\n", encoding="utf-8",
            )
            _commit(root)
            g_full = _discover_single_repo(root, incremental=False, write_graph=True)

        self.assertEqual(
            _strip_meta_graph(g_inc), _strip_meta_graph(g_full),
            "incremental graph nodes/edges/meta (sans discovered_from) must "
            "match a full discover at the same state",
        )

    def test_callgraph_dirty_callee_clean_caller_matches_full(self) -> None:
        # ADR 0074 amendment guard (the failing class the provenance-keyed
        # edge purge fixes): a single-glob ``python_callgraph`` config where
        # a CLEAN caller points at a CALLEE in the edited (dirty) file. The
        # inbound ``calls`` edge must survive the purge and the incremental
        # graph must be byte-identical to a full discover. (Pre-amendment
        # this lost ``symbol:py:pkg.caller:main -> symbol:py:pkg.callee:run``
        # because the purge dropped edges by endpoint membership and the
        # clean caller was no longer re-parsed.)
        def _cg_fixture(root: Path, body: str) -> None:
            pkg = root / "pkg"
            pkg.mkdir(exist_ok=True)
            (pkg / "__init__.py").write_text("", encoding="utf-8")
            (pkg / "callee.py").write_text(
                f"def run():\n    return {body}\n", encoding="utf-8",
            )
            (pkg / "caller.py").write_text(
                "from pkg.callee import run\n\n\ndef main():\n    return run()\n",
                encoding="utf-8",
            )
            (root / ".weld").mkdir(exist_ok=True)
            (root / ".weld" / "discover.yaml").write_text(
                "sources:\n  - strategy: python_callgraph\n"
                "    glob: pkg/*.py\n    type: symbol\n",
                encoding="utf-8",
            )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            _cg_fixture(root, "1")
            _commit(root)
            _discover_single_repo(root, incremental=False, write_graph=True)
            _cg_fixture(root, "2")  # edit only the callee body
            _commit(root)
            g_inc = _discover_single_repo(root, incremental=True, write_graph=True)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            _cg_fixture(root, "2")
            _commit(root)
            g_full = _discover_single_repo(root, incremental=False, write_graph=True)

        inbound = [
            e for e in g_inc.get("edges", [])
            if e.get("type") == "calls"
            and e["from"] == "symbol:py:pkg.caller:main"
            and e["to"] == "symbol:py:pkg.callee:run"
        ]
        self.assertTrue(
            inbound, "clean-caller -> dirty-callee calls edge lost on the "
            "incremental path (ADR 0074 provenance-purge regression)",
        )
        self.assertEqual(
            _strip_meta_graph(g_inc), _strip_meta_graph(g_full),
            "single-glob callgraph incremental graph must be byte-identical "
            "to a full discover at the same source state",
        )

    def test_no_change_second_run_is_byte_stable(self) -> None:
        # After seeding, a second incremental run with no edits must take the
        # no-change fast path and return a graph whose nodes/edges equal the
        # first run -- the shallow-copy path must not perturb content. A
        # blank ``__init__.py`` in the fixture is a node-less file recorded
        # in ``files_with_no_nodes``, exercising the exemption end-to-end.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            _fixture(root)
            _commit(root)
            g1 = _discover_single_repo(root, incremental=False, write_graph=True)
            graph_before = (root / ".weld" / "graph.json").read_bytes()
            g2 = _discover_single_repo(root, incremental=True, write_graph=True)
            self.assertEqual(g1.get("nodes"), g2.get("nodes"))
            self.assertEqual(g1.get("edges"), g2.get("edges"))
            # graph.json body is content-addressable: a no-change refresh
            # must not rewrite it to different bytes.
            self.assertEqual(
                graph_before, (root / ".weld" / "graph.json").read_bytes(),
                "no-change refresh must leave graph.json byte-identical",
            )


def _dep_edges(graph: dict) -> set[tuple[str, str]]:
    """Return the ``(from, to)`` set of ``depends_on`` (import-closure) edges."""
    return {
        (e["from"], e["to"])
        for e in graph.get("edges", [])
        if e.get("type") == "depends_on"
    }


class ModuleIndexOrderInvarianceTest(unittest.TestCase):
    """``_module_index`` resolves identically regardless of node-dict order.

    Root-cause guard for bd 89kn: ``_module_index`` fills its module->node
    map with ``setdefault`` (first-writer-wins). A module with several symbol
    nodes (a stdlib package like ``json`` -> ``dump``/``dumps``/``loads``)
    must bind ``index['json']`` to the SAME node whether the input dict is in
    full-discover insertion order or the incremental path's merge order, else
    ``import json`` resolves to a different ``depends_on`` target per path and
    the graphs drift. The fix sorts the iteration; this test pins it.
    """

    def _stdlib_nodes(self) -> dict[str, dict]:
        # Three stdlib symbol nodes for module ``json``, exactly the shape
        # ``python_callgraph`` emits for unresolved stdlib call targets.
        out: dict[str, dict] = {}
        for sym in ("dump", "dumps", "loads"):
            out[f"symbol:py:json:{sym}"] = {
                "type": "symbol",
                "label": sym,
                "props": {"language": "python", "module": "json", "origin": "stdlib"},
            }
        return out

    def test_winner_is_order_independent(self) -> None:
        nodes = self._stdlib_nodes()
        forward = _module_index(dict(nodes), {})
        reverse = _module_index(
            {k: nodes[k] for k in reversed(list(nodes))}, {},
        )
        self.assertEqual(forward.get("json"), reverse.get("json"))
        # Sorted-key first-writer-wins picks the lexicographically smallest id.
        self.assertEqual(forward.get("json"), "symbol:py:json:dump")


class IncrementalDependsOnByteIdentityTest(unittest.TestCase):
    """Incremental refresh == full for ``depends_on`` (bd 89kn repro).

    A hermetic miniature of the corpus-scale drift. ``import json`` resolves
    via ``_module_index`` whose ``setdefault`` first-writer-wins picks a target
    by node-iteration order, and a stdlib package exposes several symbol nodes
    (``dump``/``dumps``/``load``/``loads``). The two discovery paths iterate in
    different orders:

    * full -- ``python_callgraph`` emits symbols in call-DISCOVERY order, so the
      first json call across the run (``alpha.py`` -> ``json.loads``) wins;
    * incremental -- surviving prior nodes load in canonical (sorted) order, so
      the lex-first member ``json.dump`` (defined by the edited ``zulu.py``,
      re-emitted last) wins, while the prior ``-> loads`` edges also survive the
      purge.

    Pre-fix that yields ``-> dump`` AND ``-> loads`` edges on the incremental
    side against only ``-> loads`` on full. The fixture pins the orderings so
    the divergence is reproducible, not luck-of-the-dict. ``_module_index``
    sorting its iteration collapses both paths to the same winner.
    """

    def _fixture(self, root: Path, sentinel: int) -> None:
        pkg = root / "pkg"
        pkg.mkdir(exist_ok=True)
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        # Clean files sort first and are discovered first on full -> their
        # member (``loads``) is full's winner. ``zulu.py`` is the edited
        # (dirty) file; it defines the lex-first member ``dump`` -> the
        # incremental sorted-survivor winner. Divergent winners are the bug.
        (pkg / "alpha.py").write_text(
            "import json\n\n\ndef a():\n    return json.loads('a')\n",
            encoding="utf-8",
        )
        (pkg / "bravo.py").write_text(
            "import json\n\n\ndef b():\n    return json.load('b')\n",
            encoding="utf-8",
        )
        (pkg / "zulu.py").write_text(
            f"import json\n# v{sentinel}\n\n\ndef z():\n    return json.dump('z')\n",
            encoding="utf-8",
        )
        (root / ".weld").mkdir(exist_ok=True)
        (root / ".weld" / "discover.yaml").write_text(
            "sources:\n  - strategy: python_module\n    glob: pkg/*.py\n"
            "    type: file\n  - strategy: python_callgraph\n    glob: pkg/*.py\n"
            "    type: symbol\n",
            encoding="utf-8",
        )

    def test_incremental_depends_on_matches_full(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            self._fixture(root, 1)
            _commit(root)
            _discover_single_repo(root, incremental=False, write_graph=True)
            self._fixture(root, 99)  # edit only editable.py's body
            _commit(root)
            g_inc = _discover_single_repo(root, incremental=True, write_graph=True)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            self._fixture(root, 99)
            _commit(root)
            g_full = _discover_single_repo(root, incremental=False, write_graph=True)

        inc_dep, full_dep = _dep_edges(g_inc), _dep_edges(g_full)
        self.assertEqual(
            inc_dep, full_dep,
            "incremental depends_on edges drifted from full discover "
            f"(inc-only={sorted(inc_dep - full_dep)}, "
            f"full-only={sorted(full_dep - inc_dep)})",
        )
        # Whole nodes/edges identity is the stronger contract the issue names.
        self.assertEqual(
            _strip_meta_graph(g_inc), _strip_meta_graph(g_full),
            "incremental graph nodes/edges must match a full discover",
        )


if __name__ == "__main__":
    unittest.main()
