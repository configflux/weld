"""bd ki4u: a BM25 document is a node, on every backend.

The three query impls were documented as scoring with "the same BM25 math", and
they did share K1 and B -- but not the document-frequency term. Impls #1 and #2
count the distinct NODES whose tokens contain a term
(``_sqlite_query_bm25._document_frequency`` is ``COUNT(DISTINCT node_id)``);
impl #3 counted the distinct VOCABULARY ENTRIES containing it.

Those are different numbers for the same corpus, and not by a constant factor. A
node is indexed under its whole id, its label, its path, and each of their
separator-split parts, so one node routinely contributes several tokens matching
one substring term. A term concentrated on a few nodes but spread across many of
their tokens was therefore scored as if it were common -- and since ``df`` was
being subtracted from a ``total_docs`` counted in nodes, the two sides of the
formula were not even in the same unit.

Observed as: same graph, same query, same shared rank key, different node at
rank 1 depending on which backend answered. bd cgj3's demotions happened to
dominate the pair that first exposed it, which is why this needed a pin of its
own rather than a corpus entry -- any pair the demotions do not separate could
still flip, and one did as soon as bd ph1g extended the eval corpus.
"""

from __future__ import annotations

import math
import unittest

from weld._federation_eager_bm25 import _idf

#: One node, indexed under four tokens that all contain "graph" -- the ordinary
#: shape, not a contrived one: an id, a label, a path, and a split part.
_ONE_NODE_MANY_TOKENS = {
    "file:weld/graph": [("child", "file:weld/graph", 1)],
    "graph": [("child", "file:weld/graph", 1)],
    "weld/graph.py": [("child", "file:weld/graph", 1)],
    "graph_query": [("child", "file:weld/graph", 1)],
    "unrelated": [("child", "file:weld/other", 1)],
}


class DocumentFrequencyCountsNodesTest(unittest.TestCase):
    """The unit of ``df`` must be the unit of ``total_docs``."""

    def test_four_tokens_on_one_node_are_one_document(self) -> None:
        cache: dict[str, int] = {}
        _idf("graph", cache, total_docs=100, postings=_ONE_NODE_MANY_TOKENS)
        self.assertEqual(
            cache["graph"], 1,
            "df counted tokens, not nodes -- the defect bd ki4u names",
        )

    def test_two_nodes_sharing_a_token_are_two_documents(self) -> None:
        postings = {
            "shared": [("child", "file:a", 1), ("child", "file:b", 1)],
        }
        cache: dict[str, int] = {}
        _idf("shared", cache, total_docs=100, postings=postings)
        self.assertEqual(cache["shared"], 2)

    def test_the_same_node_id_in_two_children_is_two_documents(self) -> None:
        """Federation keys documents by ``(child, node)``, so a shared local id
        in two repos is two documents -- which is what ``total_docs`` counts."""
        postings = {"t": [("left", "file:a", 1), ("right", "file:a", 1)]}
        cache: dict[str, int] = {}
        _idf("t", cache, total_docs=100, postings=postings)
        self.assertEqual(cache["t"], 2)

    def test_a_rarer_term_scores_higher_than_a_common_one(self) -> None:
        """The property the unit error broke: IDF must still order by rarity."""
        common = [("child", f"file:n{i}", 1) for i in range(50)]
        postings = {
            "rare": [("child", "file:rare", 1)],
            "common": common,
        }
        cache: dict[str, int] = {}
        rare = _idf("rare", cache, total_docs=100, postings=postings)
        common_idf = _idf("common", cache, total_docs=100, postings=postings)
        self.assertGreater(rare, common_idf)

    def test_an_absent_term_contributes_nothing(self) -> None:
        cache: dict[str, int] = {}
        self.assertEqual(
            _idf("missing", cache, total_docs=100, postings=_ONE_NODE_MANY_TOKENS),
            0.0,
        )

    def test_the_cache_is_consulted_rather_than_recounted(self) -> None:
        """``df_cache`` is per-query; a seeded value must win over the postings.

        Pinned because the count is now a set build rather than a sum, and a
        rewrite that dropped the cache would be invisible in output while
        turning one scan per term into one per term per node.
        """
        cache = {"graph": 7}
        expected = math.log(1 + (100 - 7 + 0.5) / (7 + 0.5))
        self.assertAlmostEqual(
            _idf("graph", cache, total_docs=100, postings=_ONE_NODE_MANY_TOKENS),
            expected,
        )


if __name__ == "__main__":
    unittest.main()
