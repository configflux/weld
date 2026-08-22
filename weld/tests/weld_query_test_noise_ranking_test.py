"""bd to8x: a test must not outrank the code it covers on a prose query.

The reported failure, reproduced as a graph fixture: ``wd query "graph.json
serialization write json dump indent"`` returned eight
``tools/lint_terminal_safety_test.py`` symbols -- tests *about* ``json.dumps``
-- above ``weld.serializer.dumps_graph``, the documented single funnel every
canonical ``graph.json`` emitter goes through. The funnel sat at rank 31 of 57
and so never appeared inside the default limit of 20.

The mechanism is naming, not scoring: a test states its subject in a sentence
(``test_unwrapped_graph_dump_is_flagged`` carries json + serializer + graph +
dump) while the subject states itself in a word (``dumps_graph``). So the
fixture keeps the real asymmetry -- verbose test names, terse production names
-- rather than asserting on a rank number, which would pin the ranker's
arithmetic instead of its contract.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from weld._test_paths import (
    is_test_node,
    looks_like_test_path,
    query_names_tests,
    test_noise_demotion,
)
from weld.graph import Graph

_MEASURED_QUERY = "graph.json serialization write json dump indent"

_FUNNEL = "symbol:py:weld.serializer:dumps_graph"
_TEST_NOISE = (
    "symbol:py:tools.lint_terminal_safety_test:"
    "JsonSerializerBoundaryTest.test_unwrapped_graph_dump_is_flagged"
)


def _symbol(label: str, file_path: str, module: str) -> dict:
    return {
        "type": "symbol",
        "label": label,
        "props": {
            "authority": "derived",
            "confidence": "definite",
            "file": file_path,
            "kind": "function",
            "language": "python",
            "module": module,
            "origin": "project",
            "qualname": label,
            "roles": ["implementation"],
            "source_strategy": "python_callgraph",
        },
    }


def _fixture_nodes() -> dict:
    """The reported collision and nothing else.

    Both symbols are ``authority=derived, confidence=definite,
    roles=["implementation"]`` because that is what discovery really stamps:
    ``python_callgraph`` marks every symbol it mints as implementation, so a
    symbol defined inside a test module is indistinguishable from production
    code by props alone. Only the file path separates them.
    """
    return {
        _FUNNEL: _symbol(
            "dumps_graph", "weld/serializer.py", "weld.serializer"
        ),
        _TEST_NOISE: _symbol(
            "JsonSerializerBoundaryTest.test_unwrapped_graph_dump_is_flagged",
            "tools/lint_terminal_safety_test.py",
            "tools.lint_terminal_safety_test",
        ),
        "symbol:py:tools.lint_terminal_safety_test:"
        "JsonBoundaryTest.test_json_dumps_written_to_stdout_is_flagged": _symbol(
            "JsonBoundaryTest.test_json_dumps_written_to_stdout_is_flagged",
            "tools/lint_terminal_safety_test.py",
            "tools.lint_terminal_safety_test",
        ),
        "test-target://weld/tests:weld_serializer_test": {
            "type": "test-target",
            "label": "//weld/tests:weld_serializer_test",
            "props": {
                "authority": "derived",
                "confidence": "definite",
                "origin": "project",
            },
        },
    }


class _FixtureGraph:
    """Context manager yielding a loaded :class:`Graph` over *nodes*.

    Defaults to the to8x collision (:func:`_fixture_nodes`) so the existing
    call sites (``with _FixtureGraph() as graph:``) are unchanged; bd ikof's
    tests pass their own node set to exercise a different fixture through
    the same temp-dir/load boilerplate rather than duplicating it.
    """

    def __init__(self, nodes: dict | None = None) -> None:
        self._nodes = nodes if nodes is not None else _fixture_nodes()

    def __enter__(self) -> Graph:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        (root / ".weld").mkdir()
        (root / ".weld" / "graph.json").write_text(
            json.dumps({
                "meta": {"version": 1, "updated_at": "2026-08-16T00:00:00Z"},
                "nodes": self._nodes,
                "edges": [],
            }),
            encoding="utf-8",
        )
        graph = Graph(root)
        graph.load()
        return graph

    def __exit__(self, *exc: object) -> None:
        self._tmp.cleanup()


def _ranked_ids(term: str, limit: int = 20, nodes: dict | None = None) -> list[str]:
    with _FixtureGraph(nodes) as graph:
        return [
            m["id"] for m in graph.query(term, limit=limit).get("matches", [])
        ]


class MeasuredQueryRegressionTest(unittest.TestCase):
    """The reported query, asserted on order rather than on a rank integer."""

    def test_funnel_outranks_every_lint_test_symbol(self) -> None:
        ranked = _ranked_ids(_MEASURED_QUERY)
        self.assertIn(_FUNNEL, ranked)
        funnel_rank = ranked.index(_FUNNEL)
        test_ranks = [
            i for i, nid in enumerate(ranked)
            if "lint_terminal_safety_test" in nid
        ]
        self.assertTrue(test_ranks, ranked)
        self.assertLess(funnel_rank, min(test_ranks), ranked)

    def test_test_material_is_demoted_not_dropped(self) -> None:
        """A re-rank, never a filter: the tests are still in the answer."""
        ranked = _ranked_ids(_MEASURED_QUERY)
        self.assertIn(_TEST_NOISE, ranked)

    def test_test_target_nodes_are_demoted_too(self) -> None:
        """A Bazel target has no props.file, so node type has to catch it."""
        ranked = _ranked_ids(_MEASURED_QUERY)
        if "test-target://weld/tests:weld_serializer_test" in ranked:
            self.assertLess(
                ranked.index(_FUNNEL),
                ranked.index("test-target://weld/tests:weld_serializer_test"),
            )


class TestNamingQueryGuardTest(unittest.TestCase):
    """Asking for tests must still return tests first."""

    def test_query_naming_tests_disables_the_demotion(self) -> None:
        ranked = _ranked_ids("json dump test", limit=20)
        test_ranks = [
            i for i, nid in enumerate(ranked)
            if "lint_terminal_safety_test" in nid
        ]
        self.assertTrue(test_ranks, ranked)
        self.assertEqual(min(test_ranks), 0, ranked)

    def test_guard_reads_the_typed_token_not_the_expanded_group(self) -> None:
        """``expand_token_groups`` puts synonyms after the raw token.

        The group for ``test`` is ``[test, spec, fixture, mock, assert,
        unittest, pytest, tests]``; scanning whole groups would let the
        synonym table silently widen this guard as it grows.
        """
        self.assertTrue(query_names_tests([["test", "spec", "mock"]]))
        self.assertFalse(query_names_tests([["dump", "dumps"]]))
        self.assertFalse(
            query_names_tests([["serializer", "test", "spec"]]),
            "a synonym in a non-test group must not trip the guard",
        )


class TestPathPredicateTest(unittest.TestCase):
    """The shared predicate, which arch_lint_orphan now imports."""

    def test_recognised_conventions(self) -> None:
        for path in (
            "tools/lint_terminal_safety_test.py",
            "pkg/foo_test.go",
            "src/a.test.ts",
            "src/a.test.tsx",
            "weld/tests/thing.py",
            "src/__tests__/a.ts",
            "pkg/test_thing.py",
        ):
            self.assertTrue(looks_like_test_path(path), path)

    def test_production_paths_are_not_tests(self) -> None:
        for path in ("weld/serializer.py", "src/latest.ts", "", "a/contest.py"):
            self.assertFalse(looks_like_test_path(path), path)

    def test_roles_tag_is_honoured_without_a_path(self) -> None:
        self.assertTrue(is_test_node({"props": {"roles": ["test"]}}))
        self.assertFalse(is_test_node({"props": {"roles": ["implementation"]}}))

    def test_demotion_is_inert_when_the_query_names_tests(self) -> None:
        node = {"type": "symbol", "props": {"file": "a/b_test.py"}}
        self.assertEqual(test_noise_demotion(node, [["dump"]]), 1)
        self.assertEqual(test_noise_demotion(node, [["test"]]), 0)


# bd ikof: ``wd query "incremental discovery equivalence full"`` matched none
# of the six ``incremental_*_equivalence_test.py`` files at all -- test_peer
# never gave a test file ``props.summary``, and even after fixing that, the
# demotion sorted every test node behind every non-test node regardless of
# match strength (147+ non-test nodes matching the live graph's generic
# "incremental" vocabulary alone). See weld._test_paths for the mechanism.
_EQUIV_QUERY = "incremental discovery equivalence full"
_EQUIV_TEST_FILE = "file:pkg/tests/incremental_refresh_equivalence_test"
_WEAKER_PROD_FILE = "file:pkg/_incremental_purge"


def _summary_file(label: str, file_path: str, summary: str, roles: list[str]) -> dict:
    return {
        "type": "file",
        "label": label,
        "props": {
            "authority": "derived",
            "confidence": "definite",
            "file": file_path,
            "roles": roles,
            "source_strategy": "test_peer" if "test" in roles else "python_module",
            "summary": summary,
        },
    }


def _equivalence_fixture_nodes() -> dict:
    """The bd ikof scenario plus bd to8x's own noise, in one fixture.

    Combining them in one graph is the point: the exemption must fire for
    the equivalence test (summary genuinely adds a group beyond its
    filename) while leaving bd to8x's noise (same query shape, no summary
    at all) demoted, in the same query pass.
    """
    nodes = dict(_fixture_nodes())
    nodes[_EQUIV_TEST_FILE] = _summary_file(
        "incremental_refresh_equivalence_test",
        "pkg/tests/incremental_refresh_equivalence_test.py",
        # Real opening line of weld/tests/incremental_refresh_equivalence_test.py,
        # verbatim. "incremental" and "equivalence" are already in the filename;
        # only "full" comes from this summary -- the exact shape that must earn
        # the exemption without needing full 4/4 coverage from summary alone.
        "Incremental refresh is byte-equivalent to a full discover (bd 85tb.2).",
        ["test"],
    )
    nodes[_WEAKER_PROD_FILE] = _summary_file(
        "_incremental_purge",
        "pkg/_incremental_purge.py",
        # Real opening line of weld/_incremental_purge.py: 2 of 4 groups
        # (incremental, discovery), fewer than the test file's 3.
        "Provenance-aware edge purge for incremental discovery (ADR 0074).",
        ["implementation"],
    )
    return nodes


class SummaryEarnedTestExemptionTest(unittest.TestCase):
    """A test file's own summary can earn it out of the demotion (bd ikof)."""

    def test_equivalence_test_outranks_a_weaker_production_match(self) -> None:
        """Higher genuine coverage now beats the flat test/non-test split.

        Before this fix, ``_WEAKER_PROD_FILE`` (2 groups, non-test) would
        unconditionally outrank ``_EQUIV_TEST_FILE`` (3 groups, test) --
        demotion sits ahead of group count in the sort key. The exemption
        only fires because the test file's summary genuinely contributes a
        group ("full") beyond what its filename alone carries.
        """
        ranked = _ranked_ids(_EQUIV_QUERY, nodes=_equivalence_fixture_nodes())
        self.assertIn(_EQUIV_TEST_FILE, ranked)
        self.assertIn(_WEAKER_PROD_FILE, ranked)
        self.assertLess(
            ranked.index(_EQUIV_TEST_FILE), ranked.index(_WEAKER_PROD_FILE), ranked
        )

    def test_to8x_noise_stays_demoted_in_the_same_fixture(self) -> None:
        """The exemption must not blunt the original to8x fix.

        Same combined fixture, bd to8x's own measured query: the funnel
        must still lead every lint-test symbol, because that symbol's
        coverage comes entirely from its verbose qualname -- it carries no
        ``props.summary`` at all, so the exemption's "summary is
        load-bearing" half never fires for it.
        """
        ranked = _ranked_ids(_MEASURED_QUERY, nodes=_equivalence_fixture_nodes())
        funnel_rank = ranked.index(_FUNNEL)
        test_ranks = [
            i for i, nid in enumerate(ranked)
            if "lint_terminal_safety_test" in nid
        ]
        self.assertTrue(test_ranks, ranked)
        self.assertLess(funnel_rank, min(test_ranks), ranked)

    def test_demotion_unaffected_without_the_new_keywords(self) -> None:
        """The default call shape (no nid/group_hits) is untouched.

        This is what :func:`weld.ranking.rank_query_matches` (the
        strict-AND + coverage-admission path) calls, and it must keep
        demoting a test node outright even when the node's summary would
        satisfy the exemption -- that path never opts in.
        """
        node = {
            "type": "file",
            "props": {
                "file": "pkg/tests/incremental_refresh_equivalence_test.py",
                "roles": ["test"],
                "summary": (
                    "Incremental refresh is byte-equivalent to a full "
                    "discover (bd 85tb.2)."
                ),
            },
        }
        token_groups = [
            ["incremental"], ["discovery"], ["equivalence"], ["full"],
        ]
        self.assertEqual(test_noise_demotion(node, token_groups), 1)

    def test_exemption_requires_high_coverage_not_just_any_summary_hit(self) -> None:
        """One weak group from summary alone must not earn the exemption.

        ADR 0075's own ``max(2, N-1)`` admission bar, reused rather than
        invented: below it, a test node is not a strong enough match for
        this to matter, so it must stay demoted even though its summary
        does contribute the one group it carries.
        """
        node = {
            "type": "file",
            "props": {
                "file": "pkg/tests/unrelated_test.py",
                "roles": ["test"],
                "summary": "Exercises full snapshot restore.",
            },
        }
        token_groups = [
            ["incremental"], ["discovery"], ["equivalence"], ["full"],
        ]
        # Only "full" hits (via summary) -- 1 of 4, below max(2, 3) = 3.
        self.assertEqual(
            test_noise_demotion(node, token_groups, nid="x", group_hits=1), 1
        )

    def test_exemption_requires_the_summary_to_be_load_bearing(self) -> None:
        """High coverage from identity fields alone must not earn it either.

        Mirrors bd to8x/atcb's own adversarial shape: a verbose qualname
        can reach the same coverage bar with no summary at all. Stripping
        an empty summary changes nothing, so the node stays demoted.
        """
        node = {
            "type": "symbol",
            "props": {
                "file": "pkg/tests/incremental_equivalence_full_test.py",
                "qualname": "test_incremental_discovery_equivalence_full",
                "roles": ["test"],
            },
        }
        token_groups = [
            ["incremental"], ["discovery"], ["equivalence"], ["full"],
        ]
        self.assertEqual(
            test_noise_demotion(
                node, token_groups,
                nid="symbol:py:pkg.tests:test_incremental_discovery_equivalence_full",
                group_hits=4,
            ),
            1,
        )


if __name__ == "__main__":
    unittest.main()
