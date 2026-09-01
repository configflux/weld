"""The two cross-repo endpoint id spaces, and the one family that spells them.

ADR 0137 ss1-2. A federated root holds child-namespaced ids
(``<child>\\x1f<local>``, resolved inside that child) and root-minted repo ids
(``repo:<name>``, resolved in the root). ``<child>\\x1frepo:<child>`` is in
neither space, and every edge two resolvers emitted with it dangled.

What is asserted here is the part that keeps it fixed rather than the fix
itself: that the builder and every parser agree, including on the repo-level
shape the three ad-hoc separator splits used to miss entirely. Those splits
were not merely incomplete -- each one answered "this edge touches no child"
about an edge between two repositories, which is the answer that let a
drifted-child guard, an incremental invalidation, and an override warning all
pass over the very edges they exist to catch.
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr

from weld._federation_endpoints import (
    REPO_NODE_PREFIX,
    edge_child_names,
    endpoint_child_name,
    prefix_node_id,
    repo_node_id,
)
from weld.cross_repo.base import CrossRepoEdge, _edge_touches_child
from weld.cross_repo.incremental import _edge_children
from weld.cross_repo.overrides import Override, apply_overrides
from weld.federation_root import _build_repo_node
from weld.workspace import ChildEntry

_SEP = "\x1f"


def _edge(from_id: str, to_id: str) -> CrossRepoEdge:
    return CrossRepoEdge(from_id, to_id, "cross_repo:depends_on", {})


class RepoNodeIdTest(unittest.TestCase):
    """The root-minted id, and the one place it is built."""

    def test_repo_node_id_is_the_plain_root_spelling(self) -> None:
        self.assertEqual(repo_node_id("libs-order-schema"), "repo:libs-order-schema")
        self.assertEqual(REPO_NODE_PREFIX, "repo:")

    def test_no_child_namespace_is_applied(self) -> None:
        """The defect, stated as an assertion.

        ``<child>\\x1frepo:<child>`` asked a reader to resolve a root-minted id
        inside a child graph that has never held one.
        """
        self.assertNotIn(_SEP, repo_node_id("libs-order-schema"))

    def test_federation_root_mints_exactly_this_id(self) -> None:
        """The builder and the root agree, which is the whole point of ss2.

        A resolver points at a node ``federation_root`` mints. If the two
        spellings can drift, they eventually do -- they did.
        """
        node_id, body = _build_repo_node(ChildEntry(name="svc", path="services/svc"))
        self.assertEqual(node_id, repo_node_id("svc"))
        self.assertEqual(body["type"], "repo")


class EndpointChildNameTest(unittest.TestCase):
    """One parser, both shapes."""

    def test_namespaced_endpoint_names_its_child(self) -> None:
        self.assertEqual(endpoint_child_name(prefix_node_id("api", "route:GET:/x")), "api")

    def test_repo_endpoint_names_its_child(self) -> None:
        self.assertEqual(endpoint_child_name(repo_node_id("api")), "api")

    def test_other_root_ids_name_no_child(self) -> None:
        for node_id in ("file:weld/graph.py", "symbol:py:weld.graph:Graph", ""):
            self.assertIsNone(endpoint_child_name(node_id), node_id)

    def test_malformed_ids_name_no_child(self) -> None:
        """An empty half is not a child name, and neither is a bare prefix."""
        for node_id in (_SEP + "local", "repo:"):
            self.assertIsNone(endpoint_child_name(node_id), repr(node_id))

    def test_extra_separators_belong_to_the_local_half(self) -> None:
        """ADR 0011 ss7 splits once: the child name is the first half only."""
        self.assertEqual(endpoint_child_name(f"api{_SEP}a{_SEP}b"), "api")


class EdgeChildNamesTest(unittest.TestCase):
    def test_both_shapes_on_one_edge(self) -> None:
        names = edge_child_names(repo_node_id("api"), prefix_node_id("auth", "route:x"))
        self.assertEqual(names, {"api", "auth"})

    def test_self_edge_yields_one_name(self) -> None:
        self.assertEqual(edge_child_names(repo_node_id("api"), repo_node_id("api")), {"api"})

    def test_root_only_edge_yields_nothing(self) -> None:
        self.assertEqual(edge_child_names("file:a.py", "file:b.py"), set())


class EdgeTouchesChildTest(unittest.TestCase):
    """``run_resolvers``' TOCTOU guard drops edges naming a drifted child."""

    def test_repo_level_endpoints_are_seen(self) -> None:
        """The regression: a prefix test saw only namespaced ids.

        Every edge ``package_graph`` and ``compose_topology`` emit is
        repo-level, so the guard waved all of them through no matter which
        child had changed underneath the run.
        """
        edge = _edge(repo_node_id("api"), repo_node_id("schema"))
        self.assertTrue(_edge_touches_child(edge, "api"))
        self.assertTrue(_edge_touches_child(edge, "schema"))
        self.assertFalse(_edge_touches_child(edge, "unrelated"))

    def test_namespaced_endpoints_still_seen(self) -> None:
        edge = _edge(prefix_node_id("api", "http_client:c"), prefix_node_id("auth", "route:t"))
        self.assertTrue(_edge_touches_child(edge, "api"))
        self.assertTrue(_edge_touches_child(edge, "auth"))
        self.assertFalse(_edge_touches_child(edge, "ap"))


class EdgeChildrenTest(unittest.TestCase):
    """The incremental path's "which children invalidate this edge" set."""

    def test_repo_level_endpoints_are_counted(self) -> None:
        edge = _edge(repo_node_id("api"), repo_node_id("schema"))
        self.assertEqual(_edge_children(edge), {"api", "schema"})

    def test_mixed_shapes_are_counted(self) -> None:
        edge = _edge(repo_node_id("api"), prefix_node_id("auth", "route:t"))
        self.assertEqual(_edge_children(edge), {"api", "auth"})


class OverrideChildValidationTest(unittest.TestCase):
    """An override naming a child that does not exist is warned about."""

    @staticmethod
    def _add(to_id: str) -> Override:
        return Override(
            from_id=repo_node_id("api"),
            to_id=to_id,
            type="cross_repo:depends_on",
            action="add",
        )

    def test_repo_level_override_naming_an_unknown_child_is_skipped(self) -> None:
        """Before ADR 0137 ss2 this override was applied in silence.

        The validator asked ``_extract_child_name`` which children the entry
        named; on a repo-level endpoint the separator split answered "none",
        so a typo in a child name minted an edge to a node nothing holds
        instead of a warning.
        """
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            result = apply_overrides(
                [], [self._add(repo_node_id("nosuchchild"))],
                known_children=frozenset({"api"}),
            )

        self.assertEqual(result, [])
        self.assertIn("nosuchchild", stderr.getvalue())

    def test_repo_level_override_naming_known_children_is_applied(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            result = apply_overrides(
                [], [self._add(repo_node_id("schema"))],
                known_children=frozenset({"api", "schema"}),
            )

        self.assertEqual(
            [(e.from_id, e.to_id) for e in result],
            [("repo:api", "repo:schema")],
        )
        self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
