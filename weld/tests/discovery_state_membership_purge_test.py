"""Unit contract for the membership-anchored package-node purge (bd g7rs).

``weld.discovery_state.purge_stale_nodes`` purges nodes by ``props.file``.
``python_package``'s node carries ``props.dir`` instead, and
``csharp_package``'s carries neither ``dir`` nor ``file`` at all -- so
deleting every member file of a package/namespace correctly purges the
member ``file:`` nodes and their now-dangling ``contains`` edges (the
existing rule), but never the package node itself, which used to survive as
a zero-edge orphan.

These tests exercise :func:`weld.discovery_state.purge_stale_nodes` and
:func:`weld._discover_membership_purge.emptied_membership_node_ids` directly
against small synthetic graphs -- the same level ``incremental_rel_path_form_test``
and ``discover_state_case_preservation_test`` already test this function at
-- rather than only through a full ``discover()`` pipeline, so the
python_package shape, the csharp_package shape (no tree-sitter dependency
needed for a synthetic graph), the negative space (external-dependency
sentinels that legitimately have zero *outgoing* edges), and the composition
with ADR 0074 fourth amendment's orphan-edge widen-and-retry are each pinned
independently of any one strategy's real extraction code.
"""

from __future__ import annotations

import unittest

from weld._discover_membership_purge import emptied_membership_node_ids
from weld._discover_orphan_edges import orphaned_producer_files
from weld.discovery_state import purge_stale_nodes


def _package_node(*, dir_: str | None = None, roles: list[str] | None = None,
                   strategy: str = "python_package") -> dict:
    props: dict = {"source_strategy": strategy, "confidence": "definite"}
    if dir_ is not None:
        props["dir"] = dir_
    if roles is not None:
        props["roles"] = roles
    return {"type": "package", "label": "pkg", "props": props}


def _file_node(rel_path: str) -> dict:
    return {"type": "file", "label": rel_path, "props": {"file": rel_path}}


def _contains(frm: str, to: str) -> dict:
    return {"from": frm, "to": to, "type": "contains",
            "props": {"source_strategy": "python_package"}}


class EmptiedMembershipNodeIdsTest(unittest.TestCase):
    """The pure predicate, isolated from the purge call site."""

    def test_zero_out_edges_and_package_role_is_emptied(self) -> None:
        nodes = {"package:python:pkg": _package_node(dir_="pkg", roles=["package"])}
        self.assertEqual(emptied_membership_node_ids(nodes, []), {"package:python:pkg"})

    def test_surviving_contains_edge_is_not_emptied(self) -> None:
        nodes = {"package:python:pkg": _package_node(dir_="pkg", roles=["package"])}
        edges = [_contains("package:python:pkg", "file:pkg/__init__")]
        self.assertEqual(emptied_membership_node_ids(nodes, edges), set())

    def test_non_package_role_is_never_emptied_even_at_zero_edges(self) -> None:
        """External-dependency sentinels (cpp_conan, cpp_vcpkg, cmake, the C#
        using-import node) mint ``type: package`` too, but with
        ``roles: ["config"]`` (or none) -- files point AT them via inbound
        ``depends_on``, so zero *outgoing* edges is their normal steady
        state, not a defect. Keying on ``roles`` rather than bare ``type``
        is what keeps this purge from eating them."""
        nodes = {"package:cpp:zlib": _package_node(roles=["config"], strategy="cpp_conan")}
        self.assertEqual(emptied_membership_node_ids(nodes, []), set())

    def test_package_type_with_no_roles_at_all_is_never_emptied(self) -> None:
        nodes = {"package:weld": {"type": "package", "label": "weld", "props": {}}}
        self.assertEqual(emptied_membership_node_ids(nodes, []), set())

    def test_csharp_shaped_node_with_neither_dir_nor_file_is_emptied(self) -> None:
        """The mechanism csharp_package needs: its node carries no ``dir``
        prop (a namespace's members can live in any directory) and no
        ``file`` prop either, so only the roles+edge-count predicate -- not
        a props.dir presence check -- can ever reach it."""
        nodes = {
            "package:csharp:ns": _package_node(
                roles=["package"], strategy="csharp_package",
            ),
        }
        self.assertNotIn("dir", nodes["package:csharp:ns"]["props"])
        self.assertNotIn("file", nodes["package:csharp:ns"]["props"])
        self.assertEqual(
            emptied_membership_node_ids(nodes, []), {"package:csharp:ns"},
        )

    def test_non_contains_out_edge_does_not_count_as_membership(self) -> None:
        """Only ``contains`` proves membership; some other outgoing edge
        kind must not mask an empty package."""
        nodes = {"package:python:pkg": _package_node(dir_="pkg", roles=["package"])}
        edges = [{"from": "package:python:pkg", "to": "package:python:other",
                   "type": "depends_on", "props": {}}]
        self.assertEqual(
            emptied_membership_node_ids(nodes, edges), {"package:python:pkg"},
        )


class PurgeStaleNodesMembershipTest(unittest.TestCase):
    """The integrated call site: purge_stale_nodes folds the new check in."""

    def test_full_deletion_purges_the_package_node_too(self) -> None:
        nodes = {
            "file:pkg/__init__": _file_node("pkg/__init__.py"),
            "file:pkg/mod": _file_node("pkg/mod.py"),
            "package:python:pkg": _package_node(dir_="pkg", roles=["package"]),
        }
        edges = [
            _contains("package:python:pkg", "file:pkg/__init__"),
            _contains("package:python:pkg", "file:pkg/mod"),
        ]
        surviving_nodes, surviving_edges = purge_stale_nodes(
            dict(nodes), list(edges), {"pkg/__init__.py", "pkg/mod.py"},
        )
        self.assertEqual(surviving_nodes, {})
        self.assertEqual(surviving_edges, [])

    def test_partial_deletion_keeps_the_node_and_its_surviving_edge(self) -> None:
        """No over-purge: a package with a surviving member keeps its node,
        with exactly the edge to the file that is still there."""
        nodes = {
            "file:pkg/__init__": _file_node("pkg/__init__.py"),
            "file:pkg/mod": _file_node("pkg/mod.py"),
            "package:python:pkg": _package_node(dir_="pkg", roles=["package"]),
        }
        edges = [
            _contains("package:python:pkg", "file:pkg/__init__"),
            _contains("package:python:pkg", "file:pkg/mod"),
        ]
        surviving_nodes, surviving_edges = purge_stale_nodes(
            dict(nodes), list(edges), {"pkg/mod.py"},
        )
        self.assertEqual(
            set(surviving_nodes), {"file:pkg/__init__", "package:python:pkg"},
        )
        self.assertEqual(
            surviving_edges,
            [_contains("package:python:pkg", "file:pkg/__init__")],
        )

    def test_csharp_shaped_full_deletion_purges_the_namespace_node(self) -> None:
        nodes = {
            "file:Foo": _file_node("Foo.cs"),
            "package:csharp:ns": _package_node(roles=["package"], strategy="csharp_package"),
        }
        edges = [_contains("package:csharp:ns", "file:Foo")]
        surviving_nodes, surviving_edges = purge_stale_nodes(
            dict(nodes), list(edges), {"Foo.cs"},
        )
        self.assertEqual(surviving_nodes, {})
        self.assertEqual(surviving_edges, [])

    def test_unrelated_external_dependency_sentinel_survives(self) -> None:
        """A conan/vcpkg-shaped sentinel with zero outgoing edges from the
        start must not be collaterally purged by an unrelated stale file
        elsewhere in the same run."""
        nodes = {
            "file:app": _file_node("app.py"),
            "package:cpp:zlib": _package_node(roles=["config"], strategy="cpp_conan"),
        }
        edges: list = []
        surviving_nodes, _ = purge_stale_nodes(dict(nodes), edges, {"app.py"})
        self.assertIn("package:cpp:zlib", surviving_nodes)

    def test_clean_provenance_inbound_edge_survives_dangling_for_the_remint_pass(self) -> None:
        """Composition with ADR 0074's fourth amendment (bd znzu): an inbound
        edge from a CLEAN file with usable provenance must not be silently
        dropped by this purge nor silently kept forever -- it survives
        dangling here, exactly the shape
        weld._discover_orphan_edges.orphaned_producer_files exists to catch
        and widen-and-retry downstream."""
        nodes = {
            "file:pkg/__init__": _file_node("pkg/__init__.py"),
            "file:pkg/mod": _file_node("pkg/mod.py"),
            "package:python:pkg": _package_node(dir_="pkg", roles=["package"]),
            "file:consumer": _file_node("consumer.py"),
        }
        edges = [
            _contains("package:python:pkg", "file:pkg/__init__"),
            _contains("package:python:pkg", "file:pkg/mod"),
            {
                "from": "file:consumer", "to": "package:python:pkg",
                "type": "depends_on",
                "props": {"provenance": {"file": "consumer.py"},
                          "source_strategy": "python_callgraph"},
            },
        ]
        surviving_nodes, surviving_edges = purge_stale_nodes(
            dict(nodes), list(edges), {"pkg/__init__.py", "pkg/mod.py"},
        )
        self.assertNotIn("package:python:pkg", surviving_nodes)
        self.assertEqual(len(surviving_edges), 1)
        self.assertEqual(surviving_edges[0]["from"], "file:consumer")
        orphaned = orphaned_producer_files(surviving_nodes, surviving_edges)
        self.assertEqual(
            orphaned, {"consumer.py"},
            "the dangling inbound edge must be picked up by the orphan-edge "
            "detector so its producer gets a widen-and-retry re-parse",
        )

    def test_unattributed_inbound_edge_is_dropped_not_left_dangling(self) -> None:
        """The safety-floor half of the same composition: an inbound edge
        with NO usable provenance must be swept here by the widened
        endpoint-membership floor, same as any other unattributable edge
        touching a purged node -- never silently retained."""
        nodes = {
            "file:pkg/__init__": _file_node("pkg/__init__.py"),
            "file:pkg/mod": _file_node("pkg/mod.py"),
            "package:python:pkg": _package_node(dir_="pkg", roles=["package"]),
            "file:consumer": _file_node("consumer.py"),
        }
        edges = [
            _contains("package:python:pkg", "file:pkg/__init__"),
            _contains("package:python:pkg", "file:pkg/mod"),
            {
                "from": "file:consumer", "to": "package:python:pkg",
                "type": "depends_on", "props": {"source_strategy": "unstamped"},
            },
        ]
        surviving_nodes, surviving_edges = purge_stale_nodes(
            dict(nodes), list(edges), {"pkg/__init__.py", "pkg/mod.py"},
        )
        self.assertNotIn("package:python:pkg", surviving_nodes)
        self.assertEqual(surviving_edges, [])

    def test_unaffected_run_is_a_no_op(self) -> None:
        """Nothing in stale_files at all -- the pre-existing early return,
        unaffected by the new logic."""
        nodes = {"package:python:pkg": _package_node(dir_="pkg", roles=["package"])}
        surviving_nodes, surviving_edges = purge_stale_nodes(dict(nodes), [], set())
        self.assertEqual(surviving_nodes, nodes)
        self.assertEqual(surviving_edges, [])


if __name__ == "__main__":
    unittest.main()
