"""Blast radius records aggregation nodes but does not walk through them.

The defect (bd arge, fixed by ADR 0107): ``_reverse_bfs`` walked every edge in
reverse without regard to the node it was standing on, so it could arrive at a
``package`` node via ``contains`` and leave it again via ``depends_on``:

    file:tools/release_mcp_handshake
      <--contains--    package:python:tools   (206 members)
      <--depends_on--  59 sibling tools/*.py

Neither edge is wrong alone; the composition is. A ``depends_on`` into a package
means "this file imports from that namespace" and nothing finer -- discovery
could not resolve which member -- so composing it with membership manufactures a
dependency nobody recorded. On the real graph that made 65 unrelated
``tools/tier_check_*`` files dependents of one release script.

The two tests that matter are the pair: the amplification is gone AND declared
build chains still propagate. Either alone is satisfied by a wrong fix -- see
the rejected alternatives in ADR 0107, both of which passed one and failed the
other.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.graph import Graph
from weld.impact_core import impact

#: ADR 0050 requires every edge producer to stamp a confidence rank.
_DEFINITE = {"confidence": "definite", "source_strategy": "test_fixture"}


def _graph(tmp: str) -> Graph:
    """Mirrors the filed shape: one package with three members, plus a build chain.

    ``pkg/target.py`` is the seed. ``pkg/sibling.py`` shares its package and
    imports from that package -- it must NOT become a dependent. The build
    target contains the seed and a test target depends on the build target --
    that chain MUST survive, because a build target's inbound ``depends_on`` is
    declared at target granularity rather than inferred.
    """
    graph = Graph(Path(tmp))
    graph.load()
    graph.add_node("file:pkg/target", "file", "target", {"file": "pkg/target.py"})
    graph.add_node("file:pkg/sibling", "file", "sibling", {"file": "pkg/sibling.py"})
    graph.add_node("file:pkg/other", "file", "other", {"file": "pkg/other.py"})
    graph.add_node("file:app/real_importer", "file", "real_importer",
                   {"file": "app/real_importer.py"})
    graph.add_node("package:python:pkg", "package", "pkg", {})
    graph.add_node("build-target://pkg:lib", "build-target", "lib", {})
    graph.add_node("test-target://pkg:lib_test", "test-target", "lib_test", {})

    # Containment: the package aggregates all three members.
    for member in ("file:pkg/target", "file:pkg/sibling", "file:pkg/other"):
        graph.add_edge("package:python:pkg", member, "contains", _DEFINITE)
    # The coarse half: siblings import "from the package", not from the seed.
    graph.add_edge("file:pkg/sibling", "package:python:pkg", "depends_on", _DEFINITE)
    graph.add_edge("file:pkg/other", "package:python:pkg", "depends_on", _DEFINITE)
    # The precise half: a real importer of the seed file itself.
    graph.add_edge("file:app/real_importer", "file:pkg/target", "depends_on", _DEFINITE)
    # The declared build chain that must survive.
    graph.add_edge("build-target://pkg:lib", "file:pkg/target", "contains", _DEFINITE)
    graph.add_edge("test-target://pkg:lib_test", "build-target://pkg:lib",
                   "depends_on", _DEFINITE)
    return graph


def _dependent_ids(result: dict) -> set[str]:
    return {
        node["id"]
        for node in [*result["direct_dependents"], *result["transitive_dependents"]]
    }


class ImpactAggregationTerminalTest(unittest.TestCase):
    """ADR 0107: aggregation nodes are recorded, never expanded through."""

    def test_package_siblings_are_not_dependents(self) -> None:
        """The defect itself: importing from a namespace is not importing a member."""
        with tempfile.TemporaryDirectory() as tmp:
            found = _dependent_ids(impact(_graph(tmp), target="pkg/target.py", depth=5))
            self.assertNotIn("file:pkg/sibling", found)
            self.assertNotIn("file:pkg/other", found)

    def test_declared_build_chain_still_propagates(self) -> None:
        """The half a coarser fix breaks.

        Cutting the ``contains`` edge type instead of keying on the node severs
        this chain: the test target is reached only *through* the build target
        that contains the seed. On the real graph that lost 17 of
        weld/graph.py's 26 affected test-targets while fixing nothing extra.
        """
        with tempfile.TemporaryDirectory() as tmp:
            found = _dependent_ids(impact(_graph(tmp), target="pkg/target.py", depth=5))
            self.assertIn("build-target://pkg:lib", found)
            self.assertIn("test-target://pkg:lib_test", found)

    def test_real_importers_are_unaffected(self) -> None:
        """A genuine reverse dependency on the file keeps its edge."""
        with tempfile.TemporaryDirectory() as tmp:
            found = _dependent_ids(impact(_graph(tmp), target="pkg/target.py", depth=5))
            self.assertIn("file:app/real_importer", found)

    def test_the_aggregation_node_is_still_reported(self) -> None:
        """Terminal means "not expanded", not "not reached".

        The package containing a changed file is a true and cheap thing to say,
        and dropping it would be a second, opposite envelope change riding the
        same fix.
        """
        with tempfile.TemporaryDirectory() as tmp:
            found = _dependent_ids(impact(_graph(tmp), target="pkg/target.py", depth=5))
            self.assertIn("package:python:pkg", found)

    def test_affected_tests_surface_survives(self) -> None:
        """The bucket ADR 0043 added, which the rejected alternative emptied."""
        with tempfile.TemporaryDirectory() as tmp:
            result = impact(_graph(tmp), target="pkg/target.py", depth=5)
            self.assertIn(
                "test-target://pkg:lib_test",
                {entry["id"] for entry in result["affected_surfaces"]["tests"]},
            )

    def test_seeding_on_the_package_itself_still_walks(self) -> None:
        """The rule is about passing *through*, not about the node as a seed.

        Asking "what depends on this package" is a legitimate question with a
        precise answer -- its declared importers -- and the terminal rule must
        not silently answer "nothing" for it.
        """
        with tempfile.TemporaryDirectory() as tmp:
            found = _dependent_ids(
                impact(_graph(tmp), target="package:python:pkg", depth=5),
            )
            self.assertIn("file:pkg/sibling", found)
            self.assertIn("file:pkg/other", found)


if __name__ == "__main__":
    unittest.main()
