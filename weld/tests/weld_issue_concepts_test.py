"""ADR 0113 unit contract: issue-derived concepts are not evidence.

The corpus gate (``weld_query_corpus_gate_test``) proves the behaviour end to
end on real queries. This pins the primitives underneath it, in particular the
two boundaries that are easy to widen by accident: the signal is *provenance*
and not label similarity, and the candidacy rule must never hand back less than
the caller already holds.
"""

from __future__ import annotations

import unittest

from weld._issue_concepts import (
    is_issue_derived_concept,
    issue_concept_demotion,
    query_names_backlog,
)
from weld._query_candidacy import only_non_evidence, relaxed_or_none
from weld.query_index import SEPARATOR_CHARS
from weld.synonyms import _separator_variants, expand_token_groups


def _bd_concept(label: str = "some-issue-title") -> dict:
    return {
        "type": "concept",
        "label": label,
        "props": {"source_strategy": "concept_from_bd", "bd_short_id": "abcd"},
    }


#: A query that does not name the backlog, so the candidacy rule applies.
_ORDINARY = expand_token_groups(["tree-sitter", "availability"])


def _code() -> dict:
    return {
        "type": "symbol",
        "label": "dumps_graph",
        "props": {"source_strategy": "python_callgraph", "file": "weld/serializer.py"},
    }


class IsIssueDerivedConceptTest(unittest.TestCase):
    def test_bd_minted_concept_is_issue_derived(self) -> None:
        self.assertTrue(is_issue_derived_concept(_bd_concept()))

    def test_hand_authored_concept_is_not(self) -> None:
        """An enrichment/``wd add-node`` concept IS evidence and stays untouched.

        Somebody asserted it about the code deliberately; only the ledger-minted
        restatement of an issue title is disqualified.
        """
        manual = {
            "type": "concept",
            "label": "repo boundary",
            "props": {"source_strategy": "manual", "authority": "manual"},
        }
        self.assertFalse(is_issue_derived_concept(manual))

    def test_code_node_is_not(self) -> None:
        self.assertFalse(is_issue_derived_concept(_code()))

    def test_a_node_with_no_props_is_not(self) -> None:
        self.assertFalse(is_issue_derived_concept({"type": "file"}))

    def test_the_signal_is_provenance_not_label_similarity(self) -> None:
        """A label that looks nothing like any query is still issue-derived.

        The rejected alternative was "demote concepts whose label is a near-copy
        of the query". Provenance is what makes the rule stable when an issue
        title is edited and drifts a token away from the query it reports.
        """
        self.assertTrue(is_issue_derived_concept(_bd_concept("totally-unrelated")))


class DemotionTest(unittest.TestCase):
    def test_issue_concept_is_demoted_on_an_ordinary_query(self) -> None:
        groups = expand_token_groups(["tree-sitter", "availability"])
        self.assertEqual(issue_concept_demotion(_bd_concept(), groups), 1)
        self.assertEqual(issue_concept_demotion(_code(), groups), 0)

    def test_demotion_is_inert_when_the_query_names_the_backlog(self) -> None:
        """Naming the backlog must still return the backlog -- a re-rank, not a filter."""
        groups = expand_token_groups(["bd", "issue", "worktree"])
        self.assertEqual(issue_concept_demotion(_bd_concept(), groups), 0)

    def test_backlog_guard_reads_the_typed_token_not_the_expanded_group(self) -> None:
        """Guarding on element 0 keeps the synonym table from widening this rule."""
        self.assertTrue(query_names_backlog(expand_token_groups(["issue"])))
        self.assertFalse(query_names_backlog(expand_token_groups(["serializer"])))


class CandidacyTest(unittest.TestCase):
    def test_all_concept_match_set_is_not_substantive(self) -> None:
        self.assertTrue(only_non_evidence([("concept:a", _bd_concept())], _ORDINARY))

    def test_one_code_node_makes_the_set_substantive(self) -> None:
        matched = [("concept:a", _bd_concept()), ("symbol:x", _code())]
        self.assertFalse(only_non_evidence(matched, _ORDINARY))

    def test_empty_match_set_is_not_treated_as_all_concepts(self) -> None:
        """The pre-existing empty-result path already relaxes; this must not
        double-claim that decision."""
        self.assertFalse(only_non_evidence([], _ORDINARY))

    def test_relaxation_returns_the_fallback_when_it_found_something(self) -> None:
        relaxed = relaxed_or_none(
            [("concept:a", _bd_concept())],
            _ORDINARY,
            lambda: {"matches": [{"id": "symbol:x"}]},
        )
        self.assertEqual(relaxed, {"matches": [{"id": "symbol:x"}]})

    def test_relaxation_declines_when_the_fallback_is_empty(self) -> None:
        """A poor match beats no match.

        Single-token queries have no OR fallback at all (OR == AND for one
        group), so relaxing there would trade a concept-only answer for an
        empty envelope -- a worse bug than the one ADR 0113 fixes.
        """
        self.assertIsNone(
            relaxed_or_none(
                [("concept:a", _bd_concept())], _ORDINARY,
                lambda: {"matches": []}
            )
        )

    def test_relaxation_handles_the_bare_list_fallback_shape(self) -> None:
        """Impl #3's ``_or_fallback`` returns ``list[dict]``, not an envelope.

        Reading ``.get("matches")`` unconditionally would raise AttributeError
        on the federation path.
        """
        self.assertEqual(
            relaxed_or_none(
                [("concept:a", _bd_concept())], _ORDINARY, lambda: [{"id": "s"}]
            ),
            [{"id": "s"}],
        )
        self.assertIsNone(
            relaxed_or_none([("concept:a", _bd_concept())], _ORDINARY, lambda: [])
        )

    def test_a_test_only_match_set_is_also_not_evidence(self) -> None:
        """bd atcb is bd pxjc with a test node in the concept's place.

        ``wd query "broken_reference diagnostics"`` matched exactly one node --
        a test *about* the diagnostic -- so strict-AND "succeeded" and the
        module that emits it was never a candidate. Deriving non-evidence from
        the demotion predicates is what makes this one rule instead of two.
        """
        test_node = {
            "type": "symbol",
            "label": "FindingSignatureTests.test_signature_uses_broken_reference",
            "props": {"file": "tools/agent_graph_audit_gate_test.py"},
        }
        self.assertTrue(only_non_evidence([("symbol:t", test_node)], _ORDINARY))

    def test_a_test_only_match_set_IS_evidence_when_the_query_names_tests(
        self,
    ) -> None:
        """Asking for tests makes a test the answer, so nothing relaxes."""
        test_node = {
            "type": "symbol",
            "label": "SomeTest.test_thing",
            "props": {"file": "tools/some_test.py"},
        }
        names_tests = expand_token_groups(["broken_reference", "test"])
        self.assertFalse(only_non_evidence([("symbol:t", test_node)], names_tests))

    def test_relaxation_declines_when_the_query_names_the_backlog(self) -> None:
        """Concepts are not evidence *unless you asked for them*.

        Keeps the two halves of ADR 0113 coherent: the rank demotion already
        declines on this condition, and relaxing here would both widen the
        result for no reason and stamp ``degraded_match: or_fallback`` on a
        strict-AND that genuinely answered the question.
        """
        backlog = expand_token_groups(["bd", "issue", "worktree"])
        self.assertIsNone(
            relaxed_or_none(
                [("concept:a", _bd_concept())], backlog,
                lambda: {"matches": [{"id": "symbol:x"}]},
            )
        )

    def test_substantive_match_set_never_triggers_the_fallback(self) -> None:
        """The thunk must not even run on the common path."""
        calls: list[int] = []

        def _fallback() -> dict:
            calls.append(1)
            return {"matches": [{"id": "x"}]}

        self.assertIsNone(
            relaxed_or_none([("symbol:x", _code())], _ORDINARY, _fallback)
        )
        self.assertEqual(calls, [])


class SeparatorVariantTest(unittest.TestCase):
    """bd pxjc: a query typed with the project's spelling must reach the code's."""

    def test_hyphen_and_underscore_are_interchangeable(self) -> None:
        self.assertIn("tree_sitter", _separator_variants("tree-sitter"))
        self.assertIn("tree-sitter", _separator_variants("tree_sitter"))

    def test_a_plain_token_yields_no_variants(self) -> None:
        self.assertEqual(_separator_variants("serializer"), [])

    def test_variants_join_the_same_group_and_add_no_and_clause(self) -> None:
        groups = expand_token_groups(["tree-sitter", "gate"])
        self.assertEqual(len(groups), 2)
        self.assertIn("tree_sitter", groups[0])

    def test_the_typed_token_stays_at_element_zero(self) -> None:
        """query_names_tests / query_names_backlog both read position 0."""
        self.assertEqual(expand_token_groups(["tree-sitter"])[0][0], "tree-sitter")


class SeparatorVariantWidenedAlphabetTest(unittest.TestCase):
    """bd 2xoj: the query side must normalize the index's WHOLE alphabet.

    bd pxjc only re-spelled ``-``/``_``, two of the six characters
    ``weld.query_index.SEPARATOR_CHARS`` actually splits an indexed field on
    (``/:.·-_``). A query token spelled with ``.``, ``/``, ``:`` or ``·``
    inherited the same match failure one separator further out -- measured
    live: ``'graph.json'`` and ``'graph_json'`` matched disjoint sets of
    indexed tokens.
    """

    def test_dot_is_interchangeable_with_underscore(self) -> None:
        self.assertIn("graph_json", _separator_variants("graph.json"))
        self.assertIn("graph.json", _separator_variants("graph_json"))

    def test_slash_colon_and_middot_are_interchangeable_with_underscore(
        self,
    ) -> None:
        for sep in "/:·":
            spelled = f"graph{sep}json"
            with self.subTest(separator=sep):
                self.assertIn("graph_json", _separator_variants(spelled))
                self.assertIn(spelled, _separator_variants("graph_json"))

    def test_every_alphabet_character_is_generated(self) -> None:
        """A token spelled with ONE separator yields a variant for every OTHER
        character in the alphabet, not just a hardcoded pair.
        """
        variants = set(_separator_variants("graph.json"))
        expected = {f"graph{sep}json" for sep in SEPARATOR_CHARS if sep != "."}
        self.assertEqual(variants, expected)

    def test_split_parts_are_never_returned_as_variants(self) -> None:
        """ADR 0113's cosmetic-match shape, rejected here too (bd 2xoj).

        'graph.json' must never widen to the bare fragments 'graph' or
        'json' -- only to whole-token re-spellings -- or a query for the
        compound would cosmetically match any node that merely mentions one
        fragment in passing.
        """
        variants = _separator_variants("graph.json")
        self.assertNotIn("graph", variants)
        self.assertNotIn("json", variants)

    def test_an_all_separator_token_yields_no_variants(self) -> None:
        """bd 2xoj: a bare punctuation token is not a compound name.

        Without this guard, a lone ``'_'`` canonicalizes and respells to
        single-character "variants" (``'/'``, ``'.'``, ...) that substring-
        match nearly every indexed token in a real graph. This is the unit
        pin for the observable contract
        ``weld_sqlite_query_test.py::test_injection_attempt_does_not_widen_to_all_nodes``
        holds end to end (``wd query "_"`` must return nothing).
        """
        for token in ("_", "-", "---", "..", "/:"):
            with self.subTest(token=token):
                self.assertEqual(_separator_variants(token), [])

    def test_bounded_regardless_of_separator_occurrences(self) -> None:
        """No combinatorial blowup: at most one variant per alphabet character.

        A token carrying every separator character at once does not get more
        variants than a token carrying just one -- the rule is a canonical-form
        join, not a per-position cross product (which would be
        ``len(SEPARATOR_CHARS) ** occurrences``).
        """
        pathological = "a.b/c:d-e_f"  # one of each of the six characters
        variants = _separator_variants(pathological)
        self.assertLessEqual(len(variants), len(SEPARATOR_CHARS))
        self.assertEqual(len(variants), len(set(variants)), "no duplicates")

    def test_bounded_on_a_long_run_of_separators(self) -> None:
        """The bound holds independent of token length, not just character
        variety -- a thousand-hyphen token still yields at most six variants.
        """
        long_token = "-".join(["x"] * 1000)
        variants = _separator_variants(long_token)
        self.assertLessEqual(len(variants), len(SEPARATOR_CHARS))

    def test_deterministic_across_calls(self) -> None:
        """Same input, same output list -- including order, not just set
        membership -- across repeated calls.
        """
        pathological = "a.b/c:d-e_f"
        first = _separator_variants(pathological)
        second = _separator_variants(pathological)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
