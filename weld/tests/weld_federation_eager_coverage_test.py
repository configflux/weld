"""ADR 0075 parity for the eager federation path (impl #3) -- bd 8rm0.4.

8rm0.3 landed bounded coverage admission + diffuse-doc demotion in impl #1
(the in-memory ``Graph`` read path). 8rm0.4 brings the two child-repo paths to
parity:

* impl #2 -- sqlite (``weld._sqlite_query``), covered in
  ``weld_sqlite_query_test``;
* impl #3 -- eager federation aggregation (``weld._federation_eager_index``),
  covered here.

This module pins three things for the eager path:

1. **Admission + demotion** -- the entity-shaped query
   ``boundary entrypoint strategy test`` (the parent 8rm0 trap) surfaces the
   3/4 strategy / test code nodes ABOVE the 4/4 diffuse determinism doc, and the
   admitted nodes carry ``partial_coverage`` while the internal ``_diffuse``
   ranking tag never leaks into the envelope. Mirrors
   ``weld_multi_token_query_test`` (impl #1).

2. **Field-set unification** -- impl #3's match surface previously omitted
   ``props.headings`` (the pre-existing drift ADR 0075 noted). A heading-only
   doc therefore matched on the lazy/sqlite path but NOT the eager path -- a
   confirmed latent eager/lazy divergence. 8rm0.4 adds ``headings`` so the two
   agree.

3. **Eager-vs-lazy parity on the entity query** -- the strongest guard: impls
   #2 and #3 must return byte-identical ranked matches for the exact query that
   previously made them diverge (eager returned ``[]``; lazy returned the doc).

The graphs are hermetic federated children built from the shared sqlite
fixtures; a child deliberately carries the host-project-shaped trap nodes,
which is exactly the case ADR 0075 part 5 flagged as the reason these paths
need parity (a child repo that IS the host project hits the same pattern).
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


from weld.federation import FederatedGraph  # noqa: E402
from weld.tests._federation_sqlite_fixtures import (  # noqa: E402
    graph_payload,
    make_workspace,
)

_BE_QUERY = "boundary entrypoint strategy test"
_SEP = "\x1f"  # federation child-id prefix separator.
_STRAT = f"alpha{_SEP}file:weld/strategies/boundary_entrypoint"
_TEST = f"alpha{_SEP}file:weld/tests/weld_boundary_entrypoint_test"
_DOC = f"alpha{_SEP}doc:docs/determinism-audit-T1a"


def _boundary_trap_child() -> dict:
    """A child carrying the impl #1 boundary_entrypoint trap (child IS weld).

    Group coverage of ``[boundary][entrypoint][strategy][test]`` (N=4):

    * strategy module -> boundary, entrypoint, strategy (via stem) = 3/4;
    * test target     -> boundary, entrypoint, test               = 3/4;
    * determinism doc -> all four via scattered headings           = 4/4 diffuse.

    Strict-AND admits only the 4/4 doc; the 3/4 code nodes are dropped by the
    intersection unless bounded coverage admission fires.
    """
    return graph_payload({
        "file:weld/strategies/boundary_entrypoint": {
            "type": "file", "label": "boundary_entrypoint",
            "props": {"file": "weld/strategies/boundary_entrypoint.py",
                      "authority": "canonical", "confidence": "definite"},
        },
        "file:weld/tests/weld_boundary_entrypoint_test": {
            "type": "file", "label": "weld_boundary_entrypoint_test",
            "props": {"file": "weld/tests/weld_boundary_entrypoint_test.py",
                      "authority": "canonical", "confidence": "definite"},
        },
        "doc:docs/determinism-audit-T1a": {
            "type": "doc", "label": "Determinism Audit T1A",
            "props": {"file": "docs/determinism-audit-T1a.md",
                      "authority": "canonical", "confidence": "definite",
                      "headings": ["Boundary handling", "Entrypoint ordering",
                                   "Strategy emission order", "Test peer wiring"]},
        },
    })


class FederationEagerCoverageAdmissionTest(unittest.TestCase):
    """ADR 0075 parity for impl #3 (eager) and the headings unification."""

    def _trap_workspace(self, root: Path) -> None:
        make_workspace(root, children=[("alpha", _boundary_trap_child(), True)])

    def test_eager_surfaces_high_coverage_code_above_diffuse_doc(self) -> None:
        """Eager path admits the 3/4 code nodes and demotes the 4/4 doc.

        Pre-8rm0.4 the eager path returned ``[]`` here (its match surface
        omitted ``headings``, so even the 4/4 doc failed the field-AND check).
        After unification + admission + demotion it mirrors impl #1.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._trap_workspace(root)
            fg = FederatedGraph(root, eager_index=True)
            try:
                self.assertIn("alpha", fg._eager_index.eager_children)
                ids = [m["id"] for m in fg.query(_BE_QUERY, limit=5)["matches"]]
                present_code = [n for n in (_STRAT, _TEST) if n in ids]
                self.assertTrue(present_code, f"no code node surfaced; {ids}")
                self.assertIn(_DOC, ids, "diffuse doc must remain present")
                self.assertLess(
                    min(ids.index(n) for n in present_code), ids.index(_DOC),
                    f"a code node must outrank the diffuse doc; {ids}",
                )
            finally:
                fg.close()

    def test_eager_admission_tags_partial_coverage(self) -> None:
        """Eager admissions carry ``partial_coverage``; ``_diffuse`` not leaked."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._trap_workspace(root)
            fg = FederatedGraph(root, eager_index=True)
            try:
                matches = {m["id"]: m
                           for m in fg.query(_BE_QUERY, limit=5)["matches"]}
                self.assertTrue(matches[_STRAT].get("partial_coverage"))
                self.assertNotIn("partial_coverage", matches[_DOC])
                for match in matches.values():
                    self.assertNotIn("_diffuse", match)
            finally:
                fg.close()

    def test_eager_matches_lazy_on_entity_query(self) -> None:
        """Eager and lazy now agree on the entity-shaped N>=3 ordering.

        The strongest parity guard: 8rm0.4 must keep impl #2 (lazy) and impl #3
        (eager) byte-identical on the exact query that previously made them
        diverge (eager returned nothing; lazy returned the diffuse doc).
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._trap_workspace(root)
            fg_lazy = FederatedGraph(root, eager_index=False)
            try:
                lazy_ids = [m["id"]
                            for m in fg_lazy.query(_BE_QUERY, limit=20)["matches"]]
            finally:
                fg_lazy.close()
            fg_eager = FederatedGraph(root, eager_index=True)
            try:
                eager_ids = [m["id"]
                             for m in fg_eager.query(_BE_QUERY, limit=20)["matches"]]
            finally:
                fg_eager.close()
            self.assertEqual(
                lazy_ids, eager_ids,
                f"eager vs lazy ranked order diverged for {_BE_QUERY!r}",
            )

    def test_eager_matches_lazy_on_heading_only_doc(self) -> None:
        """Field unification: a heading-only doc now matches on BOTH paths.

        Before 8rm0.4 impl #3 omitted ``headings``, so a doc whose only match
        was a heading token surfaced on the lazy path but NOT the eager path --
        a confirmed latent parity divergence. This pins the fix.
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = graph_payload({
                "doc:docs/heading-only": {
                    "type": "doc", "label": "plain",
                    "props": {"file": "docs/heading-only.md",
                              "headings": ["Zebra topic", "Quokka notes"]},
                },
            })
            make_workspace(root, children=[("beta", child, True)])
            fg_lazy = FederatedGraph(root, eager_index=False)
            try:
                lazy_ids = {m["id"]
                            for m in fg_lazy.query("zebra", limit=10)["matches"]}
            finally:
                fg_lazy.close()
            fg_eager = FederatedGraph(root, eager_index=True)
            try:
                eager_ids = {m["id"]
                             for m in fg_eager.query("zebra", limit=10)["matches"]}
            finally:
                fg_eager.close()
            self.assertIn(f"beta{_SEP}doc:docs/heading-only", lazy_ids)
            self.assertEqual(
                lazy_ids, eager_ids,
                "eager must match heading-only docs like lazy (headings unified)",
            )

    def test_eager_admission_inert_below_three_tokens(self) -> None:
        """N<=2 stays inert and eager-vs-lazy parity holds for the 2-token case."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._trap_workspace(root)
            fg_lazy = FederatedGraph(root, eager_index=False)
            try:
                lazy = {m["id"]
                        for m in fg_lazy.query("boundary entrypoint",
                                               limit=10)["matches"]}
            finally:
                fg_lazy.close()
            fg_eager = FederatedGraph(root, eager_index=True)
            try:
                eager = {m["id"]
                         for m in fg_eager.query("boundary entrypoint",
                                                 limit=10)["matches"]}
            finally:
                fg_eager.close()
            self.assertEqual(lazy, eager, "N=2 eager/lazy parity must hold")


if __name__ == "__main__":
    unittest.main()
