"""All four node-id read paths must resolve an ADR 0041 alias identically.

The defect (bd m4er): four read paths take a node id, and two rewrote a legacy
alias to its canonical id before looking up while two did not.

    Graph.context               -- alias-aware
    Graph.path                  -- alias-aware, both endpoints
    graph_referrers.callers     -- NOT alias-aware
    graph_referrers.references  -- NOT alias-aware

So a transcript pasting a pre-rename id kept working through ``wd context`` and
``wd path`` and reported "node not found" through ``wd callers`` and
``wd references``. ADR 0041's alias mechanism exists precisely so pasted
historical ids keep resolving; honouring it on half the paths that accept a node
id is the same class of defect as bd nywd -- two read paths disagreeing about
one node.

The parity assertion is the anti-drift pin, not the individual fixes: this file
asserts the four paths *agree*, so a fifth read path added later that forgets
aliases fails here rather than in a transcript six months on.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.graph import Graph

#: ADR 0050 requires every edge producer to stamp a confidence rank; an
#: unstamped fixture edge warns on every load and buries the test output.
_DEFINITE = {"confidence": "definite", "source_strategy": "test_fixture"}

_CANON = "symbol:py:pkg.mod:callee"
_LEGACY = "symbol:pkg.mod.callee"
_CALLER = "symbol:py:pkg.mod:caller"


def _graph(tmp: str) -> Graph:
    """A callee carrying a legacy alias, plus one caller and one build target."""
    graph = Graph(Path(tmp))
    graph.load()
    graph.add_node(
        _CANON, "symbol", "callee",
        {"qualname": "pkg.mod.callee", "file": "pkg/mod.py", "aliases": [_LEGACY]},
    )
    graph.add_node(
        _CALLER, "symbol", "caller",
        {"qualname": "pkg.mod.caller", "file": "pkg/mod.py"},
    )
    graph.add_node(
        "build-target://pkg:lib", "build-target", "lib",
        {"aliases": ["build-target://pkg:legacy_lib"]},
    )
    graph.add_node("file:pkg/mod", "file", "mod", {"file": "pkg/mod.py"})
    graph.add_edge(_CALLER, _CANON, "calls", _DEFINITE)
    graph.add_edge("file:pkg/mod", "build-target://pkg:lib", "depends_on", _DEFINITE)
    return graph


class ReferrerAliasParityTest(unittest.TestCase):
    """The four node-id read paths agree on a legacy spelling."""

    def test_all_four_read_paths_resolve_the_alias(self) -> None:
        """The pin: no read path may reject an id the others accept."""
        with tempfile.TemporaryDirectory() as tmp:
            graph = _graph(tmp)
            # Precondition: the alias is registered and is not itself a node,
            # so every pass below is genuine alias resolution.
            self.assertNotIn(_LEGACY, graph.dump()["nodes"])
            self.assertEqual(graph._alias_index.get(_LEGACY), _CANON)

            self.assertIsNotNone(
                graph.context(_LEGACY, fallback=False).get("node"),
                "context lost alias resolution",
            )
            self.assertIsNotNone(
                graph.path(_LEGACY, _CALLER).get("path"),
                "path lost alias resolution",
            )
            self.assertNotIn(
                "error", graph.callers(_LEGACY),
                "callers does not resolve ADR 0041 aliases",
            )
            self.assertNotIn(
                "error", graph.references(_LEGACY),
                "references does not resolve ADR 0041 aliases",
            )

    def test_alias_and_canonical_give_the_same_callers(self) -> None:
        """Resolving is a rewrite, so the answer must be identical, not merely non-empty."""
        with tempfile.TemporaryDirectory() as tmp:
            graph = _graph(tmp)
            via_alias = graph.callers(_LEGACY)
            via_canonical = graph.callers(_CANON)
            self.assertEqual(
                [c["id"] for c in via_alias["callers"]],
                [c["id"] for c in via_canonical["callers"]],
            )
            self.assertEqual([_CALLER], [c["id"] for c in via_alias["callers"]])
            # bd jz65r: the callers()-side counterpart of the ``matches``
            # rewrite pin below -- ``seeds`` and the per-caller ``targets``
            # attribution it enables must both carry the canonical id, not
            # the legacy alias spelling that was looked up.
            self.assertEqual([_CANON], via_alias["seeds"])
            self.assertEqual(
                [_CANON], via_alias["callers"][0]["targets"],
            )
            self.assertEqual(via_alias["seeds"], via_canonical["seeds"])

    def test_alias_and_canonical_give_the_same_references(self) -> None:
        """Same rewrite guarantee on the references path, including ``matches``."""
        with tempfile.TemporaryDirectory() as tmp:
            graph = _graph(tmp)
            via_alias = graph.references(_LEGACY)
            via_canonical = graph.references(_CANON)
            self.assertEqual(
                [c["id"] for c in via_alias["callers"]],
                [c["id"] for c in via_canonical["callers"]],
            )
            # The rewrite must reach ``matches`` too: reporting the legacy id
            # as the matched node would leak the pre-rename spelling downstream.
            self.assertEqual([_CANON], [m["id"] for m in via_alias["matches"]])
            # And the per-caller match attribution (bd nyoks) must rewrite
            # to the canonical id on both paths -- a ``targets`` entry
            # spelled ``_LEGACY`` would leak the same pre-rename spelling
            # the ``matches`` assertion above guards against.
            self.assertEqual(
                [[_CANON]], [c["targets"] for c in via_alias["callers"]],
            )
            self.assertEqual(
                [c["targets"] for c in via_alias["callers"]],
                [c["targets"] for c in via_canonical["callers"]],
            )

    def test_alias_resolves_for_a_non_symbol_node(self) -> None:
        """Aliases and the bd nywd node-id widening must compose.

        ``references`` reaches non-symbol nodes through ``inbound_referrers``
        rather than ``calls`` edges. A fix that resolved aliases only on the
        symbol branch would pass every test above and still fail here.
        """
        with tempfile.TemporaryDirectory() as tmp:
            graph = _graph(tmp)
            result = graph.references("build-target://pkg:legacy_lib")
            self.assertNotIn("error", result)
            self.assertEqual(
                ["build-target://pkg:lib"], [m["id"] for m in result["matches"]],
            )
            self.assertEqual(
                ["file:pkg/mod"], [c["id"] for c in result["callers"]],
            )

    def test_unknown_id_still_errors(self) -> None:
        """Widening resolution must not turn a genuine miss into a silent empty.

        bd nywd was filed because "weld does not know this id" and "weld knows
        it and nothing points at it" shared a spelling; alias resolution must
        not re-open that.
        """
        with tempfile.TemporaryDirectory() as tmp:
            graph = _graph(tmp)
            self.assertIn("error", graph.callers("symbol:py:pkg.mod:nope"))
            self.assertIn("error", graph.references("symbol:py:pkg.mod:nope"))

    def test_bare_name_resolution_still_works(self) -> None:
        """The alias branch must not shadow the bare-name fallback."""
        with tempfile.TemporaryDirectory() as tmp:
            graph = _graph(tmp)
            result = graph.callers("callee")
            self.assertNotIn("error", result)
            self.assertEqual([_CALLER], [c["id"] for c in result["callers"]])


if __name__ == "__main__":
    unittest.main()
