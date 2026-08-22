"""Unit contract for the edge-anchored external package purge (bd pkz2s).

``weld.graph_closure._ensure_package_node`` mints a consumer-side ``package``
node (``props.source_strategy == "graph_closure"``,
``props.authority == "external"``) for every import that does not resolve to
a local file or module -- Go's ``strings``, Python's ``os``, an npm package
never vendored in this tree. The node carries no ``props.file`` at all, so
``weld.discovery_state.purge_stale_nodes``'s ordinary ``props.file`` match
never purges it; when the last file that imported it is deleted, the node
used to survive as a zero-*inbound*-edge orphan -- the mirror image of bd
g7rs's zero-*outbound*-edge orphan (``python_package``/``csharp_package``
losing their last member).

bd ukt95 widened the underlying rule's strategy allowlist to also cover
``cpp_conan``/``cpp_vcpkg`` dependency-leaf nodes, which turned out to share
this exact edge-anchored shape despite being minted from a manifest
declaration. That coverage -- plus the C# tree-sitter using-import node,
investigated and found NOT to share the shape -- lives in the sibling module
:mod:`weld.tests.discovery_state_manifest_dependency_purge_test` (split out
to keep both files under the 400-line cap, mirroring how
``weld_csharp_inheritance_treesitter_test.py`` was split out of
``weld_csharp_treesitter_test.py``). This file keeps the original
``graph_closure``-only contract pinned independently of that widening.

These tests exercise :func:`weld._discover_external_package_purge.emptied_external_package_node_ids`,
:func:`weld._discover_external_package_purge.emptied_placeholder_node_ids`,
and :func:`weld.discovery_state.purge_stale_nodes` directly against small
synthetic graphs -- the same level ``discovery_state_membership_purge_test``
already tests the sibling mechanism at -- rather than only through a full
``discover()`` pipeline, so the composition with both the g7rs rule and
ADR 0074's widen-and-retry is pinned independently of any one language's
real extraction code.
"""

from __future__ import annotations

import unittest

from weld._discover_external_package_purge import (
    emptied_external_package_node_ids,
    emptied_placeholder_node_ids,
)
from weld._discover_orphan_edges import orphaned_producer_files
from weld.discovery_state import purge_stale_nodes


def _closure_external_package(name: str = "strings", language: str = "go") -> dict:
    return {
        "type": "package",
        "label": name,
        "props": {
            "name": name,
            "language": language,
            "external": True,
            "source_strategy": "graph_closure",
            "authority": "external",
            "confidence": "inferred",
            "origin": "stdlib",
        },
    }


def _membership_package(*, dir_: str = "pkg") -> dict:
    """A g7rs-shaped producer-side node, for the disjointness/union tests."""
    return {
        "type": "package", "label": "pkg",
        "props": {
            "source_strategy": "python_package", "confidence": "definite",
            "dir": dir_, "roles": ["package"],
        },
    }


def _file_node(rel_path: str) -> dict:
    return {"type": "file", "label": rel_path, "props": {"file": rel_path}}


def _depends_on(frm: str, to: str, *, provenance_file: str | None = None) -> dict:
    props = {"source_strategy": "graph_closure", "confidence": "inferred"}
    if provenance_file is not None:
        props["provenance"] = {"file": provenance_file}
    return {"from": frm, "to": to, "type": "depends_on", "props": props}


class EmptiedExternalPackageNodeIdsTest(unittest.TestCase):
    """The pure predicate, isolated from the purge call site."""

    def test_zero_inbound_depends_on_is_emptied(self) -> None:
        nodes = {"package:go:strings": _closure_external_package()}
        self.assertEqual(
            emptied_external_package_node_ids(nodes, []), {"package:go:strings"},
        )

    def test_surviving_inbound_depends_on_is_not_emptied(self) -> None:
        nodes = {
            "package:go:strings": _closure_external_package(),
            "file:a": _file_node("a.go"),
        }
        edges = [_depends_on("file:a", "package:go:strings")]
        self.assertEqual(emptied_external_package_node_ids(nodes, edges), set())

    def test_derived_authority_file_anchor_is_never_emptied(self) -> None:
        """``graph_closure._ensure_file_anchor`` also stamps
        ``source_strategy: "graph_closure"``, but ``authority: "derived"``,
        ``type: "file"`` -- neither the type nor the authority matches, so
        this rule cannot reach it (and does not need to: it carries
        ``props.file`` and is already purged by the ordinary rule)."""
        nodes = {
            "file:anchor": {
                "type": "file", "label": "anchor",
                "props": {
                    "file": "pkg/mod.py", "source_strategy": "graph_closure",
                    "authority": "derived", "confidence": "definite",
                    "roles": ["implementation"],
                },
            },
        }
        self.assertEqual(emptied_external_package_node_ids(nodes, []), set())

    def test_package_type_with_no_props_at_all_is_never_emptied(self) -> None:
        nodes = {"package:weld": {"type": "package", "label": "weld", "props": {}}}
        self.assertEqual(emptied_external_package_node_ids(nodes, []), set())

    def test_non_package_type_is_never_emptied(self) -> None:
        nodes = {
            "symbol:go:x": {
                "type": "symbol", "label": "x",
                "props": {"source_strategy": "graph_closure", "authority": "external"},
            },
        }
        self.assertEqual(emptied_external_package_node_ids(nodes, []), set())

    def test_non_depends_on_out_edge_does_not_count_as_inbound(self) -> None:
        """Only an INCOMING ``depends_on`` proves an importer is alive; any
        other edge kind touching the node must not mask an empty package."""
        nodes = {"package:go:strings": _closure_external_package()}
        edges = [
            {"from": "package:go:strings", "to": "package:go:other",
             "type": "depends_on", "props": {}},
            {"from": "file:a", "to": "package:go:strings",
             "type": "contains", "props": {}},
        ]
        self.assertEqual(
            emptied_external_package_node_ids(nodes, edges), {"package:go:strings"},
        )


class EmptiedPlaceholderNodeIdsTest(unittest.TestCase):
    """The union entry point :mod:`weld.discovery_state` actually calls."""

    def test_unions_both_disjoint_rules(self) -> None:
        nodes = {
            "package:python:pkg": _membership_package(),
            "package:go:strings": _closure_external_package(),
        }
        self.assertEqual(
            emptied_placeholder_node_ids(nodes, []),
            {"package:python:pkg", "package:go:strings"},
        )


class PurgeStaleNodesExternalPackageTest(unittest.TestCase):
    """The integrated call site: purge_stale_nodes folds the new check in."""

    def test_sole_importer_deletion_purges_the_package_node_too(self) -> None:
        nodes = {
            "file:a": _file_node("a.go"),
            "package:go:strings": _closure_external_package(),
        }
        edges = [_depends_on("file:a", "package:go:strings")]
        surviving_nodes, surviving_edges = purge_stale_nodes(
            dict(nodes), list(edges), {"a.go"},
        )
        self.assertEqual(surviving_nodes, {})
        self.assertEqual(surviving_edges, [])

    def test_non_sole_importer_keeps_the_node_and_its_surviving_edge(self) -> None:
        """No over-purge: an external package with a surviving importer
        keeps its node, with exactly the edge from the file that still
        imports it -- deleting ONE of two importers must not purge the
        placeholder both of them share."""
        nodes = {
            "file:a": _file_node("a.go"),
            "file:b": _file_node("b.go"),
            "package:go:strings": _closure_external_package(),
        }
        edges = [
            _depends_on("file:a", "package:go:strings"),
            _depends_on("file:b", "package:go:strings"),
        ]
        surviving_nodes, surviving_edges = purge_stale_nodes(
            dict(nodes), list(edges), {"a.go"},
        )
        self.assertEqual(
            set(surviving_nodes), {"file:b", "package:go:strings"},
        )
        self.assertEqual(
            surviving_edges, [_depends_on("file:b", "package:go:strings")],
        )

    def test_both_placeholder_rules_fire_together_in_one_purge_call(self) -> None:
        """A membership-anchored producer node AND a closure-minted
        external-package node can both become empty from the SAME purge
        call (e.g. one glob's worth of deletions) -- the union must catch
        both, not just whichever rule the caller happened to check first."""
        nodes = {
            "file:pkg/__init__": _file_node("pkg/__init__.py"),
            "package:python:pkg": _membership_package(dir_="pkg"),
            "file:a": _file_node("a.go"),
            "package:go:strings": _closure_external_package(),
        }
        edges = [
            {"from": "package:python:pkg", "to": "file:pkg/__init__",
             "type": "contains", "props": {"source_strategy": "python_package"}},
            _depends_on("file:a", "package:go:strings"),
        ]
        surviving_nodes, surviving_edges = purge_stale_nodes(
            dict(nodes), list(edges), {"pkg/__init__.py", "a.go"},
        )
        self.assertEqual(surviving_nodes, {})
        self.assertEqual(surviving_edges, [])

    def test_clean_provenance_non_depends_on_edge_survives_dangling_for_the_remint_pass(
        self,
    ) -> None:
        """Composition with ADR 0074's fourth amendment (bd znzu) -- adapted:
        this rule's purge signal IS inbound ``depends_on`` edge count, so a
        SURVIVING ``depends_on`` edge can never coexist with a purge of its
        own target (unlike g7rs, whose purge signal -- outgoing ``contains``
        -- is a different edge type/direction from what can dangle, so its
        analogous test uses a depends_on consumer). The equivalent
        composition risk here is a DIFFERENT edge kind that also happens to
        target the placeholder (e.g. a doc's ``documents`` citation): a
        clean file's own edge into a node THIS rule purges must not be
        silently dropped nor silently kept forever -- it survives dangling
        here, exactly the shape
        weld._discover_orphan_edges.orphaned_producer_files exists to catch
        and widen-and-retry downstream."""
        nodes = {
            "file:a": _file_node("a.go"),
            "package:go:strings": _closure_external_package(),
            "file:readme": _file_node("README.md"),
        }
        edges = [
            _depends_on("file:a", "package:go:strings"),
            {
                "from": "file:readme", "to": "package:go:strings",
                "type": "documents",
                "props": {"provenance": {"file": "README.md"},
                          "source_strategy": "markdown_module"},
            },
        ]
        surviving_nodes, surviving_edges = purge_stale_nodes(
            dict(nodes), list(edges), {"a.go"},
        )
        self.assertNotIn("package:go:strings", surviving_nodes)
        self.assertEqual(len(surviving_edges), 1)
        self.assertEqual(surviving_edges[0]["from"], "file:readme")
        orphaned = orphaned_producer_files(surviving_nodes, surviving_edges)
        self.assertEqual(
            orphaned, {"README.md"},
            "the dangling inbound edge must be picked up by the orphan-edge "
            "detector so its producer gets a widen-and-retry re-parse",
        )

    def test_unattributed_inbound_depends_on_edge_also_counts_as_a_live_importer(
        self,
    ) -> None:
        """This rule's signal is inbound ``depends_on`` edge COUNT after the
        provenance-purge tier has already run -- it does not re-check
        provenance itself. An edge that survived that tier only because it
        is unattributable (falls to the conservative endpoint-membership
        floor, and neither of ITS OWN endpoints was purged) counts exactly
        the same as a provenance-attributed survivor: either way the
        package node has a live importer and must not be purged -- there is
        no equivalent of g7rs's "dropped, not left dangling" case for a bare
        depends_on edge, because surviving one IS this rule's keep signal."""
        nodes = {
            "file:a": _file_node("a.go"),
            "package:go:strings": _closure_external_package(),
            "file:consumer": _file_node("consumer.go"),
        }
        unattributed_edge = {
            "from": "file:consumer", "to": "package:go:strings",
            "type": "depends_on", "props": {"source_strategy": "unstamped"},
        }
        edges = [_depends_on("file:a", "package:go:strings"), unattributed_edge]
        surviving_nodes, surviving_edges = purge_stale_nodes(
            dict(nodes), list(edges), {"a.go"},
        )
        self.assertIn("package:go:strings", surviving_nodes)
        self.assertEqual(surviving_edges, [unattributed_edge])

    def test_unaffected_run_is_a_no_op(self) -> None:
        """Nothing in stale_files at all -- the pre-existing early return,
        unaffected by the new logic."""
        nodes = {"package:go:strings": _closure_external_package()}
        surviving_nodes, surviving_edges = purge_stale_nodes(dict(nodes), [], set())
        self.assertEqual(surviving_nodes, nodes)
        self.assertEqual(surviving_edges, [])


if __name__ == "__main__":
    unittest.main()
