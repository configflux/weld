"""Integrated call-site tests for the tree-sitter package purge (bd 5ouuf;
widened to project/stdlib origins by bd cs0rt).

Split out from ``discovery_state_tree_sitter_package_purge_test.py`` to keep
both files under the 400-line cap -- the same split ukt95 and bd n4nvt
already took for ``discovery_state_external_package_purge_test.py`` /
``discovery_state_manifest_dependency_purge_test.py`` and
``discovery_state_resolved_stub_purge_test.py`` /
``discovery_state_resolved_stub_integration_test.py`` respectively. See that
sibling file's module docstring for the full mechanism writeup, the
empirical repro fixtures, and bd cs0rt's collision-safety argument; this
file exercises :func:`weld.discovery_state.purge_stale_nodes` directly (the
real call site, not just the isolated predicate), including the no-over-purge
negative space and composition with the sibling producer-anchored shape.
"""

from __future__ import annotations

import unittest

from weld.discovery_state import purge_stale_nodes


def _tree_sitter_package(
    name: str, *, language: str = "csharp", origin: str = "external",
) -> dict:
    """The ``_add_import_dependencies`` shape shared by C# and Java -- see
    the sibling ``discovery_state_tree_sitter_package_purge_test.py`` module
    docstring for the real-fingerprint provenance."""
    return {
        "type": "package", "label": name,
        "props": {
            "name": name, "language": language,
            "source_strategy": "tree_sitter", "authority": "derived",
            "confidence": "definite", "origin": origin,
        },
    }


def _producer_package(name: str = "MyApp", *, source_strategy: str = "csharp_package") -> dict:
    """A producer-side ``roles: ["package"]`` node at the SAME id namespace
    a tree-sitter shell could occupy -- disjoint from this rule on
    ``source_strategy`` alone; g7rs's own rule (zero outgoing ``contains``)
    is what protects it."""
    return {
        "type": "package", "label": name,
        "props": {
            "name": name, "language": "csharp",
            "source_strategy": source_strategy, "authority": "derived",
            "confidence": "definite", "roles": ["package"], "origin": "project",
        },
    }


def _file_node(rel_path: str) -> dict:
    return {"type": "file", "label": rel_path, "props": {"file": rel_path}}


def _depends_on(frm: str, to: str) -> dict:
    return {
        "from": frm, "to": to, "type": "depends_on",
        "props": {"source_strategy": "tree_sitter", "confidence": "definite"},
    }


class PurgeStaleNodesTreeSitterPackageTest(unittest.TestCase):
    """The integrated call site: purge_stale_nodes folds this rule in."""

    def test_last_csharp_importer_deletion_purges_both_file_and_package(self) -> None:
        """End-to-end bd 5ouuf regression, mirroring the empirical repro:
        deleting ``Program.cs`` purges its file node via the ordinary
        ``props.file`` rule, drops the ``depends_on`` edge via the
        endpoint-membership floor (unattributable -- no
        ``props.provenance.file``), and -- the leak this issue fixed -- the
        now-zero-inbound package shell must go too in the SAME call,
        matching what a fresh full discover of the same post-delete tree
        mints (nothing)."""
        nodes = {
            "file:Program": _file_node("Program.cs"),
            "package:csharp:newtonsoft.json": _tree_sitter_package("Newtonsoft.Json"),
        }
        edges = [_depends_on("file:Program", "package:csharp:newtonsoft.json")]
        surviving_nodes, surviving_edges = purge_stale_nodes(
            dict(nodes), list(edges), {"Program.cs"},
        )
        self.assertEqual(surviving_nodes, {})
        self.assertEqual(surviving_edges, [])

    def test_last_java_importer_deletion_purges_both_file_and_package(self) -> None:
        """Same regression, the Java sibling strategy -- proves the fix is
        the shared ``tree_sitter`` shape, not a C#-only patch."""
        nodes = {
            "file:App": _file_node("src/main/java/com/example/App.java"),
            "package:java:com.fasterxml.jackson.databind": _tree_sitter_package(
                "com.fasterxml.jackson.databind", language="java", origin="unresolved",
            ),
        }
        edges = [
            _depends_on(
                "file:App", "package:java:com.fasterxml.jackson.databind",
            ),
        ]
        surviving_nodes, surviving_edges = purge_stale_nodes(
            dict(nodes), list(edges), {"src/main/java/com/example/App.java"},
        )
        self.assertEqual(surviving_nodes, {})
        self.assertEqual(surviving_edges, [])

    def test_surviving_importer_keeps_the_node(self) -> None:
        """No over-purge: two files import the same package; deleting ONE
        must leave the placeholder alive, carrying only the surviving
        importer's edge -- matching what a full run over the same
        partially-emptied tree would still emit."""
        nodes = {
            "file:a": _file_node("a.cs"),
            "file:b": _file_node("b.cs"),
            "package:csharp:newtonsoft.json": _tree_sitter_package("Newtonsoft.Json"),
        }
        edges = [
            _depends_on("file:a", "package:csharp:newtonsoft.json"),
            _depends_on("file:b", "package:csharp:newtonsoft.json"),
        ]
        surviving_nodes, surviving_edges = purge_stale_nodes(
            dict(nodes), list(edges), {"a.cs"},
        )
        self.assertIn("package:csharp:newtonsoft.json", surviving_nodes)
        self.assertEqual(
            surviving_edges,
            [_depends_on("file:b", "package:csharp:newtonsoft.json")],
        )

    def test_live_importer_survives_unrelated_deletion(self) -> None:
        """No over-purge: an unrelated stale file elsewhere in the same run
        must not collaterally purge a package shell with a live importer."""
        nodes = {
            "file:unrelated": _file_node("unrelated.cs"),
            "file:live": _file_node("Live.cs"),
            "package:csharp:newtonsoft.json": _tree_sitter_package("Newtonsoft.Json"),
        }
        edges = [_depends_on("file:live", "package:csharp:newtonsoft.json")]
        surviving_nodes, surviving_edges = purge_stale_nodes(
            dict(nodes), list(edges), {"unrelated.cs"},
        )
        self.assertIn("package:csharp:newtonsoft.json", surviving_nodes)
        self.assertEqual(surviving_edges, edges)

    def test_project_origin_shell_purged_at_zero_inbound_matching_full_discover(
        self,
    ) -> None:
        """bd cs0rt: a project-origin shell at zero inbound edges from the
        start is now purged by an unrelated deletion elsewhere in the same
        call, matching what a fresh full discover of the same tree mints
        (nothing at this id) -- the gap bd 5ouuf left here (see the sibling
        purge-unit-test file's and
        :mod:`weld._discover_tree_sitter_package_purge`'s docstrings) is
        closed, not merely narrowed."""
        nodes = {
            "file:unrelated": _file_node("unrelated.cs"),
            "package:csharp:myapp.deep.nested": _tree_sitter_package(
                "MyApp.Deep.Nested", origin="project",
            ),
        }
        surviving_nodes, _ = purge_stale_nodes(dict(nodes), [], {"unrelated.cs"})
        self.assertNotIn("package:csharp:myapp.deep.nested", surviving_nodes)

    def test_project_origin_shell_with_surviving_importer_stays(self) -> None:
        """No over-purge: this is still a zero-INBOUND rule, not an
        origin-blanket purge -- a project-origin shell with a live importer
        must survive an unrelated deletion elsewhere, the same as the
        external-origin case in
        ``test_live_importer_survives_unrelated_deletion`` above."""
        nodes = {
            "file:unrelated": _file_node("unrelated.cs"),
            "file:live": _file_node("Live.cs"),
            "package:csharp:myapp.deep.nested": _tree_sitter_package(
                "MyApp.Deep.Nested", origin="project",
            ),
        }
        edges = [_depends_on("file:live", "package:csharp:myapp.deep.nested")]
        surviving_nodes, surviving_edges = purge_stale_nodes(
            dict(nodes), edges, {"unrelated.cs"},
        )
        self.assertIn("package:csharp:myapp.deep.nested", surviving_nodes)
        self.assertEqual(surviving_edges, edges)

    def test_java_project_origin_last_importer_deletion_purges_the_node(self) -> None:
        """Java symmetry at the integrated level, mirroring
        ``test_last_java_importer_deletion_purges_both_file_and_package``
        above but for project origin -- Java has no producer strategy at
        all, so this carries zero collision risk."""
        nodes = {
            "file:Consumer": _file_node("src/main/java/com/example/bar/Consumer.java"),
            "package:java:com.example.foo": _tree_sitter_package(
                "com.example.foo", language="java", origin="project",
            ),
        }
        edges = [_depends_on("file:Consumer", "package:java:com.example.foo")]
        surviving_nodes, surviving_edges = purge_stale_nodes(
            dict(nodes), list(edges), {"src/main/java/com/example/bar/Consumer.java"},
        )
        self.assertEqual(surviving_nodes, {})
        self.assertEqual(surviving_edges, [])

    def test_producer_package_never_purged_by_this_rule(self) -> None:
        """bd cs0rt's collision proof at the integrated call site, pinned at
        the EXACT id the empirical repro's tree-sitter shell leaked at
        (``package:csharp:myapp.deep.nested``): a csharp_package producer
        node sharing that id must never be purged here even at zero inbound
        depends_on edges (a producer node's own anchor is its OUTGOING
        contains edges, g7rs's rule, orthogonal to this one -- kept live
        here via ``member`` so this test isolates THIS rule rather than
        accidentally tripping g7rs's zero-outgoing-contains rule instead)."""
        nodes = {
            "file:unrelated": _file_node("unrelated.cs"),
            "file:member": _file_node("Member.cs"),
            "package:csharp:myapp.deep.nested": _producer_package("MyApp.Deep.Nested"),
        }
        edges = [
            {
                "from": "package:csharp:myapp.deep.nested", "to": "file:member",
                "type": "contains", "props": {"source_strategy": "csharp_package"},
            },
        ]
        surviving_nodes, _ = purge_stale_nodes(dict(nodes), edges, {"unrelated.cs"})
        self.assertIn("package:csharp:myapp.deep.nested", surviving_nodes)

    def test_purge_is_deterministic_across_repeated_calls(self) -> None:
        """Two independent ``purge_stale_nodes`` calls over fresh copies of
        the identical input -- three packages spanning three origins, all
        losing their only importer in the same deletion -- must converge on
        the same fixed point every time."""
        nodes = {
            "file:Program": _file_node("Program.cs"),
            "package:csharp:newtonsoft.json": _tree_sitter_package("Newtonsoft.Json"),
            "package:csharp:widget": _tree_sitter_package("Widget", origin="unresolved"),
            "package:csharp:myapp.deep.nested": _tree_sitter_package(
                "MyApp.Deep.Nested", origin="project",
            ),
        }
        edges = [
            _depends_on("file:Program", "package:csharp:newtonsoft.json"),
            _depends_on("file:Program", "package:csharp:widget"),
            _depends_on("file:Program", "package:csharp:myapp.deep.nested"),
        ]
        first = purge_stale_nodes(dict(nodes), list(edges), {"Program.cs"})
        second = purge_stale_nodes(dict(nodes), list(edges), {"Program.cs"})
        self.assertEqual(first, second)
        self.assertEqual(first, ({}, []))


if __name__ == "__main__":
    unittest.main()
