"""Unit tests for the Go inheritance edge *emission* half (``finalise``).

Split out of :mod:`weld.tests.weld_go_inherits_test` (bd rifzk) to stay under
the repo line-count cap once that file grew a provenance-stamp assertion and
an ``implements``-never-stamps-provenance regression test: the split mirrors
the production module boundary itself
(:mod:`weld.strategies._go_inherits_extract` for fact extraction vs.
:mod:`weld.strategies._go_inherits` for edge emission), not an arbitrary
chop. See the sibling module's docstring for the extraction-half coverage
(:func:`extract_file_facts`, :func:`build_caches` / :func:`stage_file`,
:func:`build_project_symbol_index`).

Covered here: :func:`finalise` -- embedding -> ``inherits`` edges,
method-set closure -> ``implements`` edges, resolved vs. unresolved
targets, the drop-when-no-declaring-symbol rule, dedup, and (bd rifzk)
the ADR 0074 ``props.provenance.file`` stamp on ``inherits`` edges (never
on ``implements``, since Go interface satisfaction aggregates a method set
across potentially multiple files with no single unambiguous producer).

The end-to-end ``wd discover`` path is asserted on a real graph by
:mod:`weld.tests.weld_go_inherits_discovery_test`; the incremental-vs-full
equivalence proof for the provenance stamp lives in
:mod:`weld.tests.incremental_inherits_provenance_purge_test`.
"""

from __future__ import annotations

import unittest

from weld.strategies._go_inherits import finalise


def _symbol(module: str, name: str) -> dict:
    return {
        "type": "symbol",
        "label": name,
        "props": {"language": "go", "module": module},
    }


class FinaliseInheritsTest(unittest.TestCase):
    def _embedding_record(self, module: str, struct: str, base_short: str,
                          base_full: str, rel_path: str = "") -> dict:
        return {
            "module_path": module,
            "rel_path": rel_path,
            "embeddings": [(struct, base_short, base_full)],
            "methods": [],
            "interfaces": {},
        }

    def test_resolved_inherits_edge_cross_module(self) -> None:
        # Circle (geometry) embeds Base (shapes) -> cross-package inherits.
        nodes = {
            "symbol:go:geometry.geometry:Circle": _symbol("geometry.geometry", "Circle"),
            "symbol:go:shapes.shapes:Base": _symbol("shapes.shapes", "Base"),
        }
        edges: list = []
        caches = {"go_inherit_records": [
            self._embedding_record(
                "geometry.geometry", "Circle", "Base", "shapes.Base",
                rel_path="geometry/geometry.go",
            ),
        ]}
        finalise(nodes, edges, caches, "tree_sitter")
        self.assertEqual(len(edges), 1)
        edge = edges[0]
        self.assertEqual(edge["from"], "symbol:go:geometry.geometry:Circle")
        self.assertEqual(edge["to"], "symbol:go:shapes.shapes:Base")
        self.assertEqual(edge["type"], "inherits")
        self.assertTrue(edge["props"]["resolved"])
        self.assertEqual(edge["props"]["confidence"], "definite")
        self.assertEqual(edge["props"]["base_name"], "shapes.Base")
        # ADR 0074 / bd rifzk: the inherits edge is attributed to the file
        # whose struct-embedding clause produced it, so an incremental
        # purge downgrades to symbol:unresolved instead of dropping the
        # edge outright when the base's own file is deleted.
        self.assertEqual(
            edge["props"]["provenance"], {"file": "geometry/geometry.go"},
        )

    def test_implements_edge_never_carries_provenance(self) -> None:
        # Go interface satisfaction aggregates a type's method set across
        # potentially MULTIPLE files in the same package, so there is no
        # single unambiguous producing file -- unlike inherits (struct
        # embedding), which is always declared at exactly one point.
        # implements must stay on the conservative endpoint-membership
        # purge floor rather than risk misattribution.
        nodes = {
            "symbol:go:p.p:T": _symbol("p.p", "T"),
            "symbol:go:p.p:Iface": _symbol("p.p", "Iface"),
        }
        edges: list = []
        records = [{
            "module_path": "p.p",
            "rel_path": "p/p.go",
            "embeddings": [],
            "methods": [("T", "Area")],
            "interfaces": {"Iface": ({"Area"}, set())},
        }]
        finalise(nodes, edges, {"go_inherit_records": records}, "tree_sitter")
        implements = [e for e in edges if e["type"] == "implements"]
        self.assertEqual(len(implements), 1)
        self.assertNotIn("provenance", implements[0]["props"])

    def test_unresolved_embedded_base_mints_sentinel(self) -> None:
        nodes = {"symbol:go:p.p:T": _symbol("p.p", "T")}
        edges: list = []
        caches = {"go_inherit_records": [
            self._embedding_record("p.p", "T", "Mutex", "sync.Mutex"),
        ]}
        finalise(nodes, edges, caches, "tree_sitter")
        self.assertIn("symbol:unresolved:Mutex", nodes)
        self.assertEqual(edges[0]["to"], "symbol:unresolved:Mutex")
        self.assertFalse(edges[0]["props"]["resolved"])
        self.assertEqual(edges[0]["props"]["confidence"], "speculative")

    def test_record_dropped_when_struct_symbol_absent(self) -> None:
        nodes = {"symbol:go:shapes.shapes:Base": _symbol("shapes.shapes", "Base")}
        edges: list = []
        caches = {"go_inherit_records": [
            self._embedding_record("p.p", "Hidden", "Base", "Base"),
        ]}
        finalise(nodes, edges, caches, "tree_sitter")
        self.assertEqual(edges, [])

    def test_duplicate_embeddings_deduped(self) -> None:
        nodes = {
            "symbol:go:p.p:T": _symbol("p.p", "T"),
            "symbol:go:p.p:Base": _symbol("p.p", "Base"),
        }
        edges: list = []
        rec = self._embedding_record("p.p", "T", "Base", "Base")
        caches = {"go_inherit_records": [rec, dict(rec)]}
        finalise(nodes, edges, caches, "tree_sitter")
        self.assertEqual(len([e for e in edges if e["type"] == "inherits"]), 1)


class FinaliseImplementsTest(unittest.TestCase):
    def _nodes(self) -> dict:
        return {
            "symbol:go:geometry.geometry:Circle": _symbol("geometry.geometry", "Circle"),
            "symbol:go:geometry.geometry:Rectangle": _symbol("geometry.geometry", "Rectangle"),
            "symbol:go:shapes.shapes:Base": _symbol("shapes.shapes", "Base"),
            "symbol:go:shapes.shapes:Shape": _symbol("shapes.shapes", "Shape"),
        }

    def _fixture_records(self) -> list[dict]:
        # geometry.go: Circle/Rectangle embed shapes.Base, define Area.
        # shapes.go: Base defines Describe; Shape requires Area+Describe.
        return [
            {
                "module_path": "geometry.geometry",
                "embeddings": [("Circle", "Base", "shapes.Base"),
                               ("Rectangle", "Base", "shapes.Base")],
                "methods": [("Circle", "Area"), ("Rectangle", "Area")],
                "interfaces": {},
            },
            {
                "module_path": "shapes.shapes",
                "embeddings": [],
                "methods": [("Base", "Describe")],
                "interfaces": {"Shape": ({"Area", "Describe"}, set())},
            },
        ]

    def test_implements_via_promoted_embedded_method(self) -> None:
        nodes = self._nodes()
        edges: list = []
        finalise(nodes, edges, {"go_inherit_records": self._fixture_records()},
                 "tree_sitter")
        implements = {
            (e["from"], e["to"]) for e in edges if e["type"] == "implements"
        }
        # Circle has own Area + promoted Describe (from embedded Base) ->
        # satisfies Shape{Area,Describe}. Same for Rectangle.
        self.assertIn(
            ("symbol:go:geometry.geometry:Circle", "symbol:go:shapes.shapes:Shape"),
            implements,
        )
        self.assertIn(
            ("symbol:go:geometry.geometry:Rectangle", "symbol:go:shapes.shapes:Shape"),
            implements,
        )
        for edge in edges:
            if edge["type"] == "implements":
                self.assertTrue(edge["props"]["resolved"])
                self.assertEqual(edge["props"]["confidence"], "definite")

    def test_implements_origin_resolved_via_index_not_record_module(self) -> None:
        # Go packages span multiple files, each with its own per-file
        # module (``geometry.geometry`` vs. ``geometry.helper``). A type
        # declared in one file may have a method in a sibling file. The
        # implementing-type origin must resolve to the *declared* symbol
        # (via the project index), not be reconstructed from the method
        # record's module -- otherwise the implements edge is dropped.
        nodes = {
            # Circle is declared in geometry.go -> this symbol id.
            "symbol:go:geometry.geometry:Circle": _symbol("geometry.geometry", "Circle"),
            "symbol:go:shapes.shapes:Shape": _symbol("shapes.shapes", "Shape"),
        }
        edges: list = []
        records = [
            # Methods of Circle live in a sibling file (different module).
            {
                "module_path": "geometry.helper",
                "embeddings": [],
                "methods": [("Circle", "Area"), ("Circle", "Describe")],
                "interfaces": {},
            },
            {
                "module_path": "shapes.shapes",
                "embeddings": [],
                "methods": [],
                "interfaces": {"Shape": ({"Area", "Describe"}, set())},
            },
        ]
        finalise(nodes, edges, {"go_inherit_records": records}, "tree_sitter")
        implements = {(e["from"], e["to"]) for e in edges if e["type"] == "implements"}
        self.assertIn(
            ("symbol:go:geometry.geometry:Circle", "symbol:go:shapes.shapes:Shape"),
            implements,
        )

    def test_no_implements_when_method_set_incomplete(self) -> None:
        # A struct missing one interface method does not implement it.
        nodes = {
            "symbol:go:p.p:Partial": _symbol("p.p", "Partial"),
            "symbol:go:p.p:Iface": _symbol("p.p", "Iface"),
        }
        edges: list = []
        records = [{
            "module_path": "p.p",
            "embeddings": [],
            "methods": [("Partial", "Area")],  # missing Describe
            "interfaces": {"Iface": ({"Area", "Describe"}, set())},
        }]
        finalise(nodes, edges, {"go_inherit_records": records}, "tree_sitter")
        self.assertEqual([e for e in edges if e["type"] == "implements"], [])

    def test_interface_does_not_implement_itself(self) -> None:
        # The interface's own symbol must not get an implements self-edge,
        # nor should an empty interface match every struct.
        nodes = {
            "symbol:go:p.p:T": _symbol("p.p", "T"),
            "symbol:go:p.p:Shape": _symbol("p.p", "Shape"),
        }
        edges: list = []
        records = [{
            "module_path": "p.p",
            "embeddings": [],
            "methods": [("T", "Area"), ("T", "Describe")],
            "interfaces": {"Shape": ({"Area", "Describe"}, set())},
        }]
        finalise(nodes, edges, {"go_inherit_records": records}, "tree_sitter")
        implements = {(e["from"], e["to"]) for e in edges if e["type"] == "implements"}
        self.assertNotIn(
            ("symbol:go:p.p:Shape", "symbol:go:p.p:Shape"), implements
        )
        self.assertIn(("symbol:go:p.p:T", "symbol:go:p.p:Shape"), implements)

    def test_empty_interface_does_not_match_all(self) -> None:
        # An interface with no required methods must not implements-edge
        # every struct (that would be noise, not signal).
        nodes = {
            "symbol:go:p.p:T": _symbol("p.p", "T"),
            "symbol:go:p.p:Empty": _symbol("p.p", "Empty"),
        }
        edges: list = []
        records = [{
            "module_path": "p.p",
            "embeddings": [],
            "methods": [("T", "Area")],
            "interfaces": {"Empty": (set(), set())},
        }]
        finalise(nodes, edges, {"go_inherit_records": records}, "tree_sitter")
        self.assertEqual([e for e in edges if e["type"] == "implements"], [])

    def test_empty_or_missing_caches_noop(self) -> None:
        edges: list = []
        finalise({}, edges, None, "tree_sitter")
        finalise({}, edges, {}, "tree_sitter")
        finalise({}, edges, {"go_inherit_records": []}, "tree_sitter")
        self.assertEqual(edges, [])


if __name__ == "__main__":
    unittest.main()
