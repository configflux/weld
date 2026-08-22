"""Unit contract for bd ukt95's widening of the edge-anchored external
package purge to cover manifest-declared dependency LEAVES, plus bd 0cobr's
follow-on widening for ``cpp_cmake``.

Split out of :mod:`weld.tests.discovery_state_external_package_purge_test`
to keep both files under the 400-line cap (mirroring how
``weld_csharp_inheritance_treesitter_test.py`` was split out of
``weld_csharp_treesitter_test.py``); see that sibling module for the
original ``graph_closure``-only (bd pkz2s) contract this widening builds on.

Investigation (bd ukt95): ``cpp_conan``/``cpp_vcpkg`` each mint a manifest
PROJECT node (``props.file`` set, ``authority: "canonical"``) plus, for every
declared dependency, a LEAF node (``package://conan/<name>/<version>``,
``package://vcpkg/<name>``, ``authority: "external"``) with no ``props.file``
at all -- its only anchor is the project's inbound ``depends_on`` edge.
Deleting the manifest purges the project node via the ordinary rule and
drops the edge via the endpoint-membership floor, but pre-fix the now-zero-
inbound leaf lingered: a fresh full discover of the same post-delete tree
mints neither node (empirically verified against real
``cpp_conan.extract``/``cpp_vcpkg.extract`` calls), so this was a genuine
incremental-vs-full divergence, the same shape bd pkz2s fixed for
``graph_closure`` placeholders. The fix folds ``cpp_conan``/``cpp_vcpkg``
into that same rule's strategy allowlist.

Follow-on investigation (bd 0cobr): ``cpp_cmake``'s ``find_package``
dependency leaf (``weld.strategies._cmake_packages.ensure_package_sentinel``
output, ``package:cpp:<name>``) shares the byte-identical shape -- no
``props.file``, ``authority: "external"``, anchored solely by the project
node's (``cpp_cmake._ensure_project_node``, ``props.file`` set) inbound
``depends_on`` edge. Empirically re-verified end-to-end through the real
orchestrator (``_discover_single_repo``, incremental vs full, over a temp
git repo with an actual ``CMakeLists.txt``): deleting the CMakeLists.txt
purged the project node and dropped the edge, but pre-fix left the leaf
behind, while a fresh full discover of the same post-delete tree minted
neither node -- the identical divergence, folded into the same allowlist.

A third minter was investigated and found NOT to share this shape: the C#
tree-sitter using-import node (``_csharp_tree_sitter._add_import_dependencies``
output, ``source_strategy: "tree_sitter"``). Empirically (real tree-sitter
parse, not mocked) its ``authority`` is ``"derived"``, never ``"external"``,
and its existence is driven purely by a surviving ``.cs`` ``using``
statement -- deleting its manifest (``.csproj``/``PackageReference``)
degrades ``props.origin`` from ``"external"`` to ``"unresolved"`` but never
removes the node. So the "manifest deleted" scenario THIS rule exists to
catch is a non-issue for it, and ``authority: "derived"`` keeps it permanently
out of reach of the ``_EDGE_ANCHORED_STRATEGIES`` allowlist tested here --
pinned below as negative space for that narrow claim only.

bd 5ouuf later gave the SAME node a separate, disjoint rule
(:mod:`weld._discover_tree_sitter_package_purge`) for the DIFFERENT scenario
this file's rule never covered: last importing ``.cs``/``.java`` file
deleted, manifest untouched -- see
:mod:`weld.tests.discovery_state_tree_sitter_package_purge_test`, not here.
The two tests below use ``origin: "project"``; bd cs0rt widened that sibling
rule to purge it too, so both now pin the split: still out of THIS file's
own rule, no longer out of the union.
"""

from __future__ import annotations

import unittest

from weld._discover_external_package_purge import (
    emptied_external_package_node_ids,
    emptied_placeholder_node_ids,
)
from weld.discovery_state import purge_stale_nodes


def _manifest_external_package(name: str = "zlib", *, strategy: str = "cpp_conan") -> dict:
    """A cpp_conan/cpp_vcpkg-shaped dependency LEAF node in isolation: also
    ``authority: "external"``, minted from a manifest declaration rather
    than purely from being imported. bd ukt95 proved this leaf is
    edge-anchored exactly like a graph_closure placeholder despite the
    manifest origin -- the manifest anchors the separate *project* node
    (:func:`_conan_project_node`), never this leaf -- so at zero inbound
    edges it is purged the same way, not exempted."""
    ecosystem = strategy.removeprefix("cpp_")
    return {
        "type": "package", "label": f"{ecosystem} {name}",
        "props": {
            "name": name, "source_strategy": strategy,
            "authority": "external", "confidence": "inferred",
            "roles": ["config"], "ecosystem": ecosystem,
        },
    }


def _conan_project_node(
    project: str = "myproj", *, file: str = "myproj/conanfile.txt",
) -> dict:
    """The manifest-anchored PROJECT node cpp_conan mints alongside each
    dependency leaf -- carries ``props.file``, so the ordinary rule purges
    IT directly when the manifest is deleted. The dependency leaf
    (:func:`_manifest_external_package`) carries no such anchor; only the
    project's inbound ``depends_on`` edge keeps it alive."""
    return {
        "type": "package", "label": project,
        "props": {
            "name": project, "file": file, "source_strategy": "cpp_conan",
            "authority": "canonical", "confidence": "definite",
            "roles": ["config"],
        },
    }


def _cmake_project_node(
    project: str = "myproj", *, file: str = "myproj/CMakeLists.txt",
) -> dict:
    """The manifest-anchored PROJECT node ``cpp_cmake._ensure_project_node``
    mints -- carries ``props.file``, purged directly by the ordinary rule
    when the CMakeLists.txt is deleted. Unlike ``cpp_conan``/``cpp_vcpkg``
    (whose leaves live under a distinct ``package://conan/``/``package://vcpkg/``
    URL scheme), this node and its ``find_package`` dependency leaf
    (:func:`_manifest_external_package` with ``strategy="cpp_cmake"``) share
    the SAME ``package:cpp:<name>`` id namespace (both go through
    :func:`weld._node_ids.package_id`) -- distinct only because a project is
    never named identically to one of its own dependencies in these
    fixtures. The purge predicate itself is id-scheme-agnostic (it keys off
    ``props.source_strategy``/``props.authority`` and inbound-edge id
    membership, never the id's shape), so this namespace overlap does not
    change the fix -- it is documented here as the one real difference in
    anchor shape empirically confirmed for bd 0cobr."""
    return {
        "type": "package", "label": project,
        "props": {
            "name": project, "file": file, "source_strategy": "cpp_cmake",
            "authority": "canonical", "confidence": "definite",
            "roles": ["config"], "build_system": "cmake",
        },
    }


def _csharp_using_import_package(name: str = "Newtonsoft.Json", *, origin: str = "external") -> dict:
    """The C# tree-sitter using-import node
    (``_csharp_tree_sitter._add_import_dependencies``'s output). bd ukt95
    verified empirically that it is ``authority: "derived"`` -- NEVER
    ``"external"`` -- so neither THIS rule nor its widened form can reach
    it, regardless of inbound edge count or ``origin``. Deleting its
    manifest (``.csproj``/``PackageReference``) does not orphan it either:
    its true anchor is a surviving ``.cs`` ``using`` statement. (bd 5ouuf
    gave this shape a SEPARATE rule; bd cs0rt widened it to cover every
    ADR 0042 origin, including ``"project"`` -- see
    :mod:`weld.tests.discovery_state_tree_sitter_package_purge_test`.)"""
    return {
        "type": "package", "label": name,
        "props": {
            "name": name, "language": "csharp",
            "source_strategy": "tree_sitter", "authority": "derived",
            "confidence": "definite", "origin": origin,
        },
    }


def _file_node(rel_path: str) -> dict:
    return {"type": "file", "label": rel_path, "props": {"file": rel_path}}


def _depends_on(frm: str, to: str, *, strategy: str = "cpp_conan") -> dict:
    return {
        "from": frm, "to": to, "type": "depends_on",
        "props": {"source_strategy": strategy, "confidence": "definite"},
    }


class ManifestDependencyLeafPurgeTest(unittest.TestCase):
    """The pure predicate, isolated from the purge call site."""

    def test_conan_dependency_leaf_zero_inbound_is_emptied(self) -> None:
        """bd ukt95: a cpp_conan dependency leaf stamps ``authority:
        "external"`` too, and turned out to be edge-anchored exactly like
        a graph_closure placeholder despite being minted from a manifest
        declaration -- the manifest anchors the separate PROJECT node
        (``props.file`` set), not this leaf. A fresh full discover of a
        tree whose conanfile.txt was deleted mints neither node, so this
        leaf must be purged at zero inbound edges, not exempted."""
        nodes = {"package://conan/zlib/unversioned": _manifest_external_package()}
        self.assertEqual(
            emptied_external_package_node_ids(nodes, []),
            {"package://conan/zlib/unversioned"},
        )

    def test_vcpkg_dependency_leaf_zero_inbound_is_also_emptied(self) -> None:
        """Same rule, the other manifest-anchored strategy bd ukt95 widened
        the allowlist to cover."""
        nodes = {
            "package://vcpkg/fmt": _manifest_external_package(
                "fmt", strategy="cpp_vcpkg",
            ),
        }
        self.assertEqual(
            emptied_external_package_node_ids(nodes, []), {"package://vcpkg/fmt"},
        )

    def test_cpp_cmake_dependency_leaf_zero_inbound_is_also_emptied(self) -> None:
        """bd 0cobr: cpp_cmake's ``find_package`` dependency leaf shares the
        identical edge-anchored shape, confirmed empirically through the
        real ``_discover_single_repo`` orchestrator (incremental vs full)
        rather than assumed from reading the strategy alone."""
        nodes = {
            "package:cpp:zlib": _manifest_external_package(
                "zlib", strategy="cpp_cmake",
            ),
        }
        self.assertEqual(
            emptied_external_package_node_ids(nodes, []), {"package:cpp:zlib"},
        )

    def test_csharp_using_import_derived_authority_is_never_emptied(self) -> None:
        """A DIFFERENT manifest-adjacent node investigated under bd ukt95 and
        found NOT to share this shape: the C# using-import node's authority
        is ``"derived"``, never ``"external"``, so the authority check alone
        excludes it regardless of ``source_strategy`` or inbound edge
        count -- see :func:`_csharp_using_import_package`."""
        nodes = {"package:csharp:newtonsoft.json": _csharp_using_import_package()}
        self.assertEqual(emptied_external_package_node_ids(nodes, []), set())


class EmptiedPlaceholderNodeIdsManifestDependencyTest(unittest.TestCase):
    """The union entry point :mod:`weld.discovery_state` actually calls."""

    def test_csharp_project_origin_node_matches_only_the_sibling_rule(self) -> None:
        """Still out of THIS file's own rule (``authority == "external"``
        required; always ``"derived"`` here), but bd cs0rt widened the
        sibling tree-sitter rule to cover ``"project"`` too, so the UNION
        entry point now purges it via that rule instead."""
        nodes = {
            "package:csharp:myapp.deep.nested": _csharp_using_import_package(
                "MyApp.Deep.Nested", origin="project",
            ),
        }
        self.assertEqual(emptied_external_package_node_ids(nodes, []), set())
        self.assertEqual(
            emptied_placeholder_node_ids(nodes, []),
            {"package:csharp:myapp.deep.nested"},
        )


class PurgeStaleNodesManifestDependencyTest(unittest.TestCase):
    """The integrated call site: purge_stale_nodes folds the widened check in."""

    def test_live_manifest_dependency_survives_unrelated_deletion(self) -> None:
        """No over-purge (bd ukt95): a cpp_conan dependency leaf with a LIVE
        inbound edge from its still-present project node must not be
        collaterally purged by an unrelated stale file elsewhere in the
        same run. This is the realistic steady state -- a dependency leaf
        never exists without its minting project's edge (both are minted
        together in the same ``_emit_dep`` call), so a zero-edge leaf
        cannot occur outside a purge already in progress (that in-progress
        case is
        ``test_manifest_file_deletion_purges_both_project_and_dependency_leaf``
        below)."""
        nodes = {
            "file:unrelated": _file_node("unrelated.cpp"),
            "package:cpp:myproj": _conan_project_node(),
            "package://conan/zlib/unversioned": _manifest_external_package(),
        }
        edges = [
            _depends_on("package:cpp:myproj", "package://conan/zlib/unversioned"),
        ]
        surviving_nodes, surviving_edges = purge_stale_nodes(
            dict(nodes), list(edges), {"unrelated.cpp"},
        )
        self.assertIn("package://conan/zlib/unversioned", surviving_nodes)
        self.assertIn("package:cpp:myproj", surviving_nodes)
        self.assertEqual(surviving_edges, edges)

    def test_manifest_file_deletion_purges_both_project_and_dependency_leaf(
        self,
    ) -> None:
        """End-to-end bd ukt95 regression, mirroring the empirical repro:
        deleting ``conanfile.txt`` purges the project node via the
        ordinary ``props.file`` rule, drops its ``depends_on`` edge via the
        endpoint-membership floor (the edge is unattributable -- no
        ``props.provenance.file``), and -- the bug this issue fixed -- the
        now-zero-inbound dependency leaf must go too in the SAME call,
        matching what a fresh full ``cpp_conan.extract()`` over the same
        post-delete tree mints (nothing)."""
        nodes = {
            "package:cpp:myproj": _conan_project_node(),
            "package://conan/zlib/unversioned": _manifest_external_package(),
        }
        edges = [
            _depends_on("package:cpp:myproj", "package://conan/zlib/unversioned"),
        ]
        surviving_nodes, surviving_edges = purge_stale_nodes(
            dict(nodes), list(edges), {"myproj/conanfile.txt"},
        )
        self.assertEqual(surviving_nodes, {})
        self.assertEqual(surviving_edges, [])

    def test_csharp_project_origin_using_import_node_now_purged_by_the_sibling_rule(
        self,
    ) -> None:
        """bd cs0rt: still out of THIS file's own rule, but
        ``purge_stale_nodes`` calls the UNION, which now purges this id at
        zero inbound edges via the widened sibling rule -- matching a fresh
        full discover of the same tree (nothing at this id)."""
        nodes = {
            "file:unrelated": _file_node("unrelated.cs"),
            "package:csharp:myapp.deep.nested": _csharp_using_import_package(
                "MyApp.Deep.Nested", origin="project",
            ),
        }
        surviving_nodes, _ = purge_stale_nodes(dict(nodes), [], {"unrelated.cs"})
        self.assertNotIn("package:csharp:myapp.deep.nested", surviving_nodes)

    def test_cpp_cmake_dependency_leaf_survives_unrelated_deletion(self) -> None:
        """No over-purge (bd 0cobr): a cpp_cmake ``find_package`` leaf with a
        LIVE inbound edge from its still-present project node must not be
        collaterally purged by an unrelated stale file elsewhere in the same
        run -- mirrors ``test_live_manifest_dependency_survives_unrelated_deletion``
        above for the cpp_conan shape."""
        nodes = {
            "file:unrelated": _file_node("unrelated.cpp"),
            "package:cpp:myproj": _cmake_project_node(),
            "package:cpp:zlib": _manifest_external_package(
                "zlib", strategy="cpp_cmake",
            ),
        }
        edges = [
            _depends_on(
                "package:cpp:myproj", "package:cpp:zlib", strategy="cpp_cmake",
            ),
        ]
        surviving_nodes, surviving_edges = purge_stale_nodes(
            dict(nodes), list(edges), {"unrelated.cpp"},
        )
        self.assertIn("package:cpp:zlib", surviving_nodes)
        self.assertIn("package:cpp:myproj", surviving_nodes)
        self.assertEqual(surviving_edges, edges)

    def test_cmakelists_deletion_purges_both_project_and_dependency_leaf(
        self,
    ) -> None:
        """bd 0cobr end-to-end regression, mirroring the empirical repro run
        against the real ``_discover_single_repo`` orchestrator: deleting
        ``CMakeLists.txt`` purges the project node via the ordinary
        ``props.file`` rule, drops its ``depends_on`` edge via the
        endpoint-membership floor, and -- the leak this issue fixed -- the
        now-zero-inbound ``find_package`` leaf must go too in the SAME call,
        matching what a fresh full ``cpp_cmake.extract()`` over the same
        post-delete tree mints (nothing). Pre-fix (``cpp_cmake`` absent from
        ``_EDGE_ANCHORED_STRATEGIES``) this assertion failed: the leaf
        survived as a zero-inbound orphan."""
        nodes = {
            "package:cpp:myproj": _cmake_project_node(),
            "package:cpp:zlib": _manifest_external_package(
                "zlib", strategy="cpp_cmake",
            ),
        }
        edges = [
            _depends_on(
                "package:cpp:myproj", "package:cpp:zlib", strategy="cpp_cmake",
            ),
        ]
        surviving_nodes, surviving_edges = purge_stale_nodes(
            dict(nodes), list(edges), {"myproj/CMakeLists.txt"},
        )
        self.assertEqual(surviving_nodes, {})
        self.assertEqual(surviving_edges, [])

    def test_cpp_cmake_purge_is_deterministic_across_repeated_calls(self) -> None:
        """Determinism (bd 0cobr): two independent ``purge_stale_nodes``
        calls over fresh copies of the identical input -- a project with TWO
        ``find_package`` leaves, both losing their only inbound edge in the
        same manifest deletion -- must converge on the same fixed point
        every time. The underlying predicate is a plain set comprehension
        with no ordering-sensitive state, but this pins that invariant at
        the integrated call site rather than assuming it holds."""
        nodes = {
            "package:cpp:myproj": _cmake_project_node(),
            "package:cpp:zlib": _manifest_external_package(
                "zlib", strategy="cpp_cmake",
            ),
            "package:cpp:fmt": _manifest_external_package(
                "fmt", strategy="cpp_cmake",
            ),
        }
        edges = [
            _depends_on(
                "package:cpp:myproj", "package:cpp:zlib", strategy="cpp_cmake",
            ),
            _depends_on(
                "package:cpp:myproj", "package:cpp:fmt", strategy="cpp_cmake",
            ),
        ]
        first = purge_stale_nodes(dict(nodes), list(edges), {"myproj/CMakeLists.txt"})
        second = purge_stale_nodes(dict(nodes), list(edges), {"myproj/CMakeLists.txt"})
        self.assertEqual(first, second)
        self.assertEqual(first, ({}, []))


if __name__ == "__main__":
    unittest.main()
