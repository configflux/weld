"""Multi-language cross-repo coverage for ``package_import_resolver``.

The original :mod:`weld.tests.weld_package_import_resolver_test` proved the
resolver wires up cleanly for a synthetic ``type="python_module"`` shape.
That fixture diverged from what discovery actually writes: real Python
``python_module`` runs and real C# tree-sitter runs both mint file nodes with
``type="file"`` and an ``imports_from`` list. The resolver's old
``if node.get("type") != "python_module": continue`` gate therefore skipped
every real-world consumer on both languages. The fix here closes that gap
as a follow-up to the language-tier polyrepo-federation criterion.

This module pins the multi-language contract on the resolver:

* Python file nodes emitted by ``python_module`` (``type="file"``) participate.
* C# file nodes emitted by the shared tree-sitter strategy (``type="file"``)
  participate.
* Mixed-language polyrepos emit cross-repo edges for both languages in the
  same run, in deterministic order.
* Node shapes that are not legitimate package consumers (``function``,
  ``symbol``, etc.) are still skipped -- the fix loosens the gate from
  "exactly one Python-flavoured type" to "any node type known to carry an
  ``imports_from`` list", not to "any node at all".

The existing ``weld_package_import_resolver_test`` regression suite keeps
its ``type="python_module"`` fixtures so the historical contract stays
exercised verbatim.
"""

from __future__ import annotations

import unittest

from weld.cross_repo.base import (
    CrossRepoEdge,
    ResolverContext,
    run_resolvers,
)

# Importing ``weld.cross_repo`` registers the resolver as a side effect.
import weld.cross_repo  # noqa: F401

SEP = "\x1f"
_STRATEGIES = ["package_import_resolver"]


class _G:
    """Minimal :class:`weld.graph.Graph` stand-in.

    Mirrors the real Graph shape used by production discovery: nodes
    live at ``_data['nodes']`` as a dict keyed by node id, with each
    value shaped ``{type, label, props}``. The pre-fix variant of this
    stub exposed a top-level ``.nodes`` property and put ``name`` /
    ``imports_from`` at the top level of each node dict -- a shape that
    never matched production output and let the cross-repo bug (bd b1k8)
    slip through.
    """

    def __init__(self, nodes: list[dict]) -> None:
        store: dict[str, dict] = {}
        for node in nodes:
            node_id = node["id"]
            inner_props = dict(node.get("props") or {})
            for key in ("name", "imports_from"):
                if key in node and key not in inner_props:
                    inner_props[key] = node[key]
            store[node_id] = {
                "type": node.get("type", ""),
                "label": node.get("label", node_id),
                "props": inner_props,
            }
        self._data = {"meta": {}, "nodes": store, "edges": []}


def _ctx(children: dict[str, tuple[_G, bytes]]):
    """Build a :class:`ResolverContext` from ``{name: (graph, raw_bytes)}``."""
    loaded = {n: g for n, (g, _) in children.items()}
    hashes = {n: ResolverContext.hash_bytes(r) for n, (_, r) in children.items()}
    return ResolverContext(
        workspace_root="/tmp/ws",
        cross_repo_strategies=list(_STRATEGIES),
        children=loaded,
        child_hashes=hashes,
    )


def _file_node(node_id: str, imports: list[str]) -> dict:
    """Real-shape file node as emitted by tree-sitter / python_module.

    Both ``python_module`` (weld/strategies/python_module.py) and the shared
    tree-sitter strategy for C# (weld/strategies/_csharp_tree_sitter.py)
    mint a node whose ``type`` is ``"file"`` and whose props carry
    ``imports_from``. The resolver must accept this shape.
    """
    return {"id": node_id, "type": "file", "imports_from": imports}


def _pkg(node_id: str, name: str) -> dict:
    """Producer node -- a ``package`` declaration that consumers can match."""
    return {"id": node_id, "type": "package", "name": name}


# ---------------------------------------------------------------------------
# C# only: ShareX-style scenario in miniature.
# ---------------------------------------------------------------------------


class CSharpPolyrepoTests(unittest.TestCase):
    """A 2-repo C# polyrepo must emit at least one cross-repo edge."""

    def test_emits_cross_repo_edge_for_csharp_file_node(self) -> None:
        # Consumer: a C# Caller.cs file that uses Foo.Bar.
        # Producer: a sibling repo that declares package:csharp:Foo.Bar.
        caller = _G([_file_node("file:Caller", ["Foo.Bar"])])
        lib = _G([_pkg("package:csharp:Foo.Bar", "Foo.Bar")])
        edges = run_resolvers(
            _ctx({"caller": (caller, b'a'), "lib": (lib, b'b')})
        )
        self.assertEqual(len(edges), 1)
        edge = edges[0]
        self.assertIsInstance(edge, CrossRepoEdge)
        self.assertEqual(edge.type, "depends_on")
        self.assertEqual(edge.from_id, f"caller{SEP}file:Caller")
        self.assertEqual(edge.to_id, f"lib{SEP}package:csharp:Foo.Bar")
        self.assertEqual(edge.props["import_name"], "Foo.Bar")
        self.assertEqual(edge.props["source_child"], "caller")
        # Per ADR 0050 / ADR 0055 review queue, name-only matches are
        # speculative regardless of language.
        self.assertEqual(edge.props["confidence"], "speculative")

    def test_csharp_file_node_with_no_imports_is_skipped(self) -> None:
        caller = _G([_file_node("file:Empty", [])])
        lib = _G([_pkg("package:csharp:Foo.Bar", "Foo.Bar")])
        self.assertEqual(
            run_resolvers(_ctx({"caller": (caller, b'a'), "lib": (lib, b'b')})),
            [],
        )

    def test_csharp_intra_repo_match_is_not_a_cross_repo_edge(self) -> None:
        # Consumer and producer live in the same child: no edge.
        graph = _G([
            _file_node("file:Caller", ["Foo.Bar"]),
            _pkg("package:csharp:Foo.Bar", "Foo.Bar"),
        ])
        self.assertEqual(run_resolvers(_ctx({"sharex": (graph, b'a')})), [])


# ---------------------------------------------------------------------------
# Python only: real shape produced by ``python_module`` strategy.
# ---------------------------------------------------------------------------


class PythonRealShapeTests(unittest.TestCase):
    """Real Python discovery emits ``type='file'``, not ``type='python_module'``.

    weld/strategies/python_module.py line 238 sets ``type='file'``. Before
    the fix the resolver missed every production Python file node despite
    the synthetic fixture in :mod:`weld_package_import_resolver_test`
    pretending otherwise.
    """

    def test_real_python_file_node_with_imports_emits_edge(self) -> None:
        caller = _G([_file_node("app/main", ["shared_utils"])])
        lib = _G([_pkg("shared_utils", "shared_utils")])
        edges = run_resolvers(
            _ctx({"app": (caller, b'a'), "lib": (lib, b'b')})
        )
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].props["import_name"], "shared_utils")


# ---------------------------------------------------------------------------
# Mixed Python + C# polyrepo.
# ---------------------------------------------------------------------------


class MixedLanguagePolyrepoTests(unittest.TestCase):
    """A 3-child polyrepo with one Python consumer + one C# consumer + a shared lib."""

    def test_mixed_polyrepo_emits_both_edges(self) -> None:
        py_caller = _G([_file_node("py:app.main", ["shared_utils"])])
        cs_caller = _G([_file_node("file:Caller", ["Foo.Bar"])])
        py_lib = _G([_pkg("shared_utils", "shared_utils")])
        cs_lib = _G([_pkg("package:csharp:Foo.Bar", "Foo.Bar")])
        edges = run_resolvers(
            _ctx({
                "py-app": (py_caller, b'a'),
                "py-lib": (py_lib, b'b'),
                "cs-app": (cs_caller, b'c'),
                "cs-lib": (cs_lib, b'd'),
            })
        )
        self.assertEqual(len(edges), 2)
        # Sort to compare independently of resolver iteration order.
        by_import = sorted(e.props["import_name"] for e in edges)
        self.assertEqual(by_import, ["Foo.Bar", "shared_utils"])
        # Ensure each edge points to the correct producer child.
        for edge in edges:
            if edge.props["import_name"] == "Foo.Bar":
                self.assertEqual(edge.from_id, f"cs-app{SEP}file:Caller")
                self.assertEqual(
                    edge.to_id, f"cs-lib{SEP}package:csharp:Foo.Bar",
                )
            else:
                self.assertEqual(edge.from_id, f"py-app{SEP}py:app.main")
                self.assertEqual(edge.to_id, f"py-lib{SEP}shared_utils")


# ---------------------------------------------------------------------------
# Consumer-type allowlist: keep non-file/non-module nodes out.
# ---------------------------------------------------------------------------


class ConsumerTypeAllowlistTests(unittest.TestCase):
    """The resolver still rejects consumer shapes that are not file/module nodes.

    The original ``NodeTypeFilterTests.test_non_python_module_ignored`` used
    ``type='function'`` to ensure non-modules are skipped. We keep that
    invariant: the fix loosens the gate to *any package-consumer file/module
    node*, not to *any node with imports_from*.
    """

    def test_function_node_with_imports_from_is_ignored(self) -> None:
        ga = _G([
            {"id": "f", "type": "function", "imports_from": ["shared_utils"]},
        ])
        gs = _G([_pkg("shared_utils", "shared_utils")])
        self.assertEqual(
            run_resolvers(_ctx({"r": (ga, b'a'), "s": (gs, b'b')})),
            [],
        )

    def test_symbol_node_with_imports_from_is_ignored(self) -> None:
        ga = _G([
            {"id": "s", "type": "symbol", "imports_from": ["shared_utils"]},
        ])
        gs = _G([_pkg("shared_utils", "shared_utils")])
        self.assertEqual(
            run_resolvers(_ctx({"r": (ga, b'a'), "s": (gs, b'b')})),
            [],
        )


# ---------------------------------------------------------------------------
# Determinism across mixed-language inputs.
# ---------------------------------------------------------------------------


class MixedLanguageDeterminismTests(unittest.TestCase):
    def test_identical_output_on_repeat(self) -> None:
        py = _G([_file_node("py:app", ["shared_utils"])])
        cs = _G([_file_node("file:Caller", ["Foo.Bar"])])
        pyl = _G([_pkg("shared_utils", "shared_utils")])
        csl = _G([_pkg("package:csharp:Foo.Bar", "Foo.Bar")])
        children = {
            "pyapp": (py, b'a'),
            "pylib": (pyl, b'b'),
            "csapp": (cs, b'c'),
            "cslib": (csl, b'd'),
        }
        first = [e.to_dict() for e in run_resolvers(_ctx(children))]
        second = [e.to_dict() for e in run_resolvers(_ctx(children))]
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
