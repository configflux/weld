"""Acceptance gate for bd ziv1: ADRs that govern a module are reachable
from that module, on this repository's own real graph.

Sibling of ``weld_cross_source_edge_provenance_repo_test.py`` and
``weld_node_edge_contract_repo_test.py`` -- same shape (a full, real,
non-incremental discovery of the host repo, ``external``-tagged because
neither the tree nor ``.weld/discover.yaml`` is a declared Bazel input),
narrower purpose: those two gates prove the *shape* of every cross-source
edge and node is contract-clean; this one proves the *specific* citations
bd ziv1 reported are actually present, in both directions, on the real
graph -- and that the one deliberate extraction-honesty exclusion (ADR
0128 §5) has not silently regressed into over-matching.

``docs/adrs/0020-exclude-semantics-and-boundary-hardening.md`` names
``weld.repo_boundary.path_within_repo_boundary`` and
``docs/adrs/0027-init-and-query-cold-path-on-large-repos.md`` names
``weld/repo_boundary.py:iter_repo_files`` -- both explicit, backtick-
quoted citations. ``docs/adrs/0012-determinism-contract.md`` (also named
in the original bd ziv1 report) contains no citation of the module at
all: it states general determinism rules the module's implementation
happens to follow, never the module's name. Minting an edge for it would
require exactly the fuzzy/thematic matching ADR 0128 rules out, so its
absence here is the correct outcome, pinned rather than left to drift.
"""

from __future__ import annotations

import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

_MODULE = "file:weld/repo_boundary"
_ADR_0020 = "doc:docs/adrs/0020-exclude-semantics-and-boundary-hardening"
_ADR_0027 = "doc:docs/adrs/0027-init-and-query-cold-path-on-large-repos"
_ADR_0012 = "doc:docs/adrs/0012-determinism-contract"


class AdrGovernsModuleEdgesTest(unittest.TestCase):
    """Against this repository's own discover.yaml and real strategies."""

    @classmethod
    def setUpClass(cls) -> None:
        # The published source tree carries the package but not the host
        # repo's own .weld/discover.yaml; skip cleanly there rather than
        # fail (same guard shape as the provenance/contract repo gates).
        if not (_REPO_ROOT / ".weld" / "discover.yaml").is_file():
            raise unittest.SkipTest("host repo .weld/discover.yaml not present")
        from weld.discover import _discover_single_repo

        cls.graph = _discover_single_repo(
            _REPO_ROOT, incremental=False, with_sqlite=False, write_graph=False,
        )
        cls.nodes = cls.graph.get("nodes", {})
        cls.edges = cls.graph.get("edges", [])

    def _documents_edge(self, from_id: str, to_id: str) -> dict | None:
        for e in self.edges:
            if (e.get("type"), e.get("from"), e.get("to")) == (
                "documents", from_id, to_id,
            ):
                return e
        return None

    def test_adr_0020_and_0027_doc_nodes_exist(self) -> None:
        self.assertIn(_ADR_0020, self.nodes, "docs/adrs/*.md must be discovered")
        self.assertIn(_ADR_0027, self.nodes)

    def test_module_has_inbound_documents_edges_from_both_adrs(self) -> None:
        """``wd context file:weld/repo_boundary`` must list both ADRs inbound."""
        inbound_docs = {
            e["from"] for e in self.edges
            if e.get("type") == "documents" and e.get("to") == _MODULE
        }
        self.assertIn(
            _ADR_0020, inbound_docs,
            "ADR 0020 explicitly names "
            "weld.repo_boundary.path_within_repo_boundary and must "
            "produce an inbound documents edge onto the module",
        )
        self.assertIn(
            _ADR_0027, inbound_docs,
            "ADR 0027 explicitly names "
            "weld/repo_boundary.py:iter_repo_files and must produce an "
            "inbound documents edge onto the module",
        )

    def test_adr_0020_lists_the_module_among_what_it_governs(self) -> None:
        """The reverse direction: ``wd context`` on the ADR node."""
        edge = self._documents_edge(_ADR_0020, _MODULE)
        self.assertIsNotNone(
            edge, "doc:docs/adrs/0020-... must have an outbound documents "
            "edge onto file:weld/repo_boundary",
        )
        self.assertEqual(edge["props"].get("source_strategy"), "markdown")
        self.assertEqual(edge["props"].get("confidence"), "inferred")
        self.assertEqual(
            edge["props"].get("provenance"),
            {"file": "docs/adrs/0020-exclude-semantics-and-boundary-hardening.md"},
        )

    def test_adr_0027_documents_edge_is_provenance_stamped(self) -> None:
        edge = self._documents_edge(_ADR_0027, _MODULE)
        self.assertIsNotNone(edge)
        self.assertEqual(
            edge["props"].get("provenance"),
            {"file": "docs/adrs/0027-init-and-query-cold-path-on-large-repos.md"},
        )

    def test_adr_0012_has_no_edge_to_the_module(self) -> None:
        """ADR 0128 §5: a deliberate, documented extraction-honesty exclusion.

        ADR 0012 never textually names weld/repo_boundary anywhere in its
        body (verified by direct grep during this task, not assumed).
        Pinned so a future loosening of the match rule toward thematic
        association cannot silently re-introduce a fuzzy edge here.
        """
        self.assertIsNone(self._documents_edge(_ADR_0012, _MODULE))

    def test_docs_adrs_directory_is_broadly_discovered(self) -> None:
        """Sanity floor: this is not just the two ADRs above passing by luck."""
        adr_doc_nodes = [
            nid for nid, node in self.nodes.items()
            if nid.startswith("doc:docs/adrs/") and node.get("type") == "doc"
        ]
        adr_files_on_disk = list((_REPO_ROOT / "docs" / "adrs").glob("*.md"))
        self.assertGreaterEqual(
            len(adr_doc_nodes), len(adr_files_on_disk) - 1,
            "docs/adrs/*.md should be broadly discovered as doc: nodes, "
            "not just the two ADRs this test names directly",
        )


if __name__ == "__main__":
    unittest.main()
