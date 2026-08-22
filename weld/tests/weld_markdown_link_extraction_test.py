"""Tests for inter-doc link extraction in the markdown strategy.

Asserts that ``[label](other.md)`` syntax in a markdown body produces
``relates_to`` edges between the source ``doc:*`` node and the target
``doc:*`` node, with defensive resolution: anchors stripped, relative
paths resolved, missing files skipped, output sorted+deduped.

Closes a doc-orphan gap surfaced during pre-release self-test where
``doc:*`` nodes for cross-referenced markdown files appeared with no
inbound or outbound edges in the graph.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


from weld.strategies.markdown import extract  # noqa: E402

_SOURCE = {
    "glob": "docs/*.md",
    "id_prefix": "doc:docs",
    "doc_kind": "guide",
}


def _setup(d: str, files: dict) -> Path:
    """Write each ``rel_path -> body`` under ``d/docs/`` and return root."""
    root = Path(d)
    docs = root / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    for rel, body in files.items():
        path = docs / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


def _relates_edges(result):
    return [e for e in result.edges if e["type"] == "relates_to"]


class InterDocLinkExtractionTest(unittest.TestCase):
    """[label](other.md) inside a doc body emits relates_to edges."""

    def test_simple_link_emits_edge(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _setup(d, {
                "a.md": "See [the guide](b.md) for details.\n",
                "b.md": "# B\n\nBody.\n",
            })
            result = extract(root, _SOURCE, {})
            edges = _relates_edges(result)
            self.assertTrue(
                any(
                    e["from"] == "doc:docs/a" and e["to"] == "doc:docs/b"
                    for e in edges
                ),
                f"missing a->b edge in {edges}",
            )

    def test_missing_target_no_edge(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _setup(d, {
                "a.md": "Broken [link](does-not-exist.md).\n",
            })
            result = extract(root, _SOURCE, {})
            edges = _relates_edges(result)
            self.assertEqual(
                edges, [],
                f"expected no edge for missing target, got {edges}",
            )

    def test_link_with_anchor_resolves_to_doc(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _setup(d, {
                "a.md": "See [section](b.md#installation).\n",
                "b.md": "# B\n",
            })
            result = extract(root, _SOURCE, {})
            edges = _relates_edges(result)
            self.assertTrue(
                any(
                    e["from"] == "doc:docs/a" and e["to"] == "doc:docs/b"
                    for e in edges
                ),
                f"anchor link did not resolve cleanly: {edges}",
            )

    def test_relative_dot_slash_resolves(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _setup(d, {
                "a.md": "See [top](./top.md).\n",
                "top.md": "# Top\n",
            })
            result = extract(root, _SOURCE, {})
            edges = _relates_edges(result)
            self.assertTrue(
                any(
                    e["from"] == "doc:docs/a" and e["to"] == "doc:docs/top"
                    for e in edges
                ),
                f"./top.md did not resolve: {edges}",
            )

    def test_external_target_outside_docs_no_edge(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _setup(d, {
                "a.md": "See [readme](../README.md) too.\n",
            })
            (root / "README.md").write_text("# Root\n", encoding="utf-8")
            result = extract(root, _SOURCE, {})
            edges = _relates_edges(result)
            # README is intentionally excluded by the strategy, so even
            # though the file exists no doc:* node is produced for it
            # and the edge target would be unresolvable. Skip silently.
            self.assertEqual(
                edges, [],
                f"link to excluded README must not emit edge: {edges}",
            )

    def test_duplicate_links_deduped(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _setup(d, {
                "a.md": (
                    "[one](b.md) and [two](b.md) and [three](b.md#x).\n"
                ),
                "b.md": "# B\n",
            })
            result = extract(root, _SOURCE, {})
            edges = [
                e for e in _relates_edges(result)
                if e["from"] == "doc:docs/a" and e["to"] == "doc:docs/b"
            ]
            self.assertEqual(
                len(edges), 1,
                f"expected dedupe to one edge, got {len(edges)}: {edges}",
            )

    def test_edges_sorted_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _setup(d, {
                "a.md": "Refs [c](c.md), [b](b.md), [d](d.md).\n",
                "b.md": "# B\n",
                "c.md": "# C\n",
                "d.md": "# D\n",
            })
            result = extract(root, _SOURCE, {})
            edges = [
                e for e in _relates_edges(result)
                if e["from"] == "doc:docs/a"
            ]
            tos = [e["to"] for e in edges]
            self.assertEqual(
                tos, sorted(tos),
                f"edges not sorted deterministically: {tos}",
            )

    def test_self_link_no_edge(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _setup(d, {
                "a.md": "Self [ref](a.md).\n",
            })
            result = extract(root, _SOURCE, {})
            edges = _relates_edges(result)
            self.assertEqual(
                edges, [],
                f"self-link should not emit edge: {edges}",
            )

    def test_non_md_link_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _setup(d, {
                "a.md": (
                    "See [code](../weld/foo.py) and "
                    "[web](https://example.com).\n"
                ),
            })
            result = extract(root, _SOURCE, {})
            edges = _relates_edges(result)
            self.assertEqual(
                edges, [],
                f"non-.md links must be ignored: {edges}",
            )


class EdgeProvenanceTest(unittest.TestCase):
    """Every relates_to edge names the file whose body held the link.

    ADR 0074 keys the incremental edge purge on ``props.provenance.file``,
    "the file that produced the edge". For this strategy that is always the
    *mentioning* doc, never the target it resolved -- see
    ``incremental_markdown_provenance_purge_test`` for why the direction is
    the load-bearing part. Asserted from a fixture where the two differ and
    a second where one file mints two edges, so a stamp accidentally lifted
    from the loop's target variable cannot pass.
    """

    def test_provenance_is_the_mentioning_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _setup(d, {
                "a.md": "See [the guide](b.md).\n",
                "b.md": "# B\n\nBody.\n",
            })
            edges = _relates_edges(extract(root, _SOURCE, {}))
            self.assertEqual(len(edges), 1, f"expected one edge: {edges}")
            self.assertEqual(
                edges[0]["props"].get("provenance"), {"file": "docs/a.md"},
                "provenance.file must be the mentioning doc (docs/a.md), "
                "not the target it resolved",
            )

    def test_every_edge_from_one_doc_shares_that_docs_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _setup(d, {
                "a.md": "See [b](b.md) and [c](c.md).\n",
                "b.md": "# B\n",
                "c.md": "# C\n",
            })
            edges = _relates_edges(extract(root, _SOURCE, {}))
            self.assertEqual(len(edges), 2, f"expected two edges: {edges}")
            self.assertEqual(
                [e["props"].get("provenance") for e in edges],
                [{"file": "docs/a.md"}] * 2,
                "both edges are produced by docs/a.md and must say so",
            )


class EdgeContractValidationTest(unittest.TestCase):
    """Emitted relates_to edges pass the contract validator."""

    def test_emitted_edges_pass_validation(self) -> None:
        from weld.contract import validate_edge

        with tempfile.TemporaryDirectory() as d:
            root = _setup(d, {
                "a.md": "Link [b](b.md).\n",
                "b.md": "# B\n",
            })
            result = extract(root, _SOURCE, {})
            node_ids = set(result.nodes.keys())
            for edge in result.edges:
                errors = validate_edge(edge, node_ids)
                self.assertEqual(errors, [], f"edge {edge}: {errors}")


class FencedLinkMintsNoEdgeTest(unittest.TestCase):
    """A link that renders as code is not a reference (bd w624).

    Sibling of bd ve41 on the heading scan, one scan later. The regex ran
    over the whole document with no fence state, so a doc that *shows* a
    markdown link in a sample minted a real ADR 0074 provenance-stamped
    ``relates_to`` edge from it. Measures zero on this repository's indexed
    globs, which is why ve41 left it: the fix is preventive, for any repo
    whose docs quote links.
    """

    def test_link_inside_a_fenced_block_emits_no_edge(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _setup(d, {
                "a.md": "How to link:\n\n```markdown\n[the guide](b.md)\n```\n",
                "b.md": "# B\n",
            })
            result = extract(root, _SOURCE, {})
            # Both doc nodes still exist -- only the phantom edge is gone.
            self.assertIn("doc:docs/b", result.nodes)
            self.assertEqual([], _relates_edges(result))

    def test_a_real_link_beside_a_sampled_one_still_emits_its_edge(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _setup(d, {
                "a.md": (
                    "See [the guide](b.md).\n\n"
                    "```markdown\n[not a reference](c.md)\n```\n"
                ),
                "b.md": "# B\n",
                "c.md": "# C\n",
            })
            result = extract(root, _SOURCE, {})
            self.assertEqual(
                [("doc:docs/a", "doc:docs/b")],
                [(e["from"], e["to"]) for e in _relates_edges(result)],
            )


if __name__ == "__main__":
    unittest.main()
