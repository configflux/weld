"""``stale_sources`` bound and reason vocabulary.

Split out of ``weld_stale_source_test`` to hold that module under the
line-count cap. Pure unit coverage, no git fixture: the cap/sort/omit
mechanics in ``weld._staleness._cap_stale_sources`` and the closed reason
vocabulary in ``weld._stale_reasons`` are both plain functions of their
inputs.
"""

from __future__ import annotations

import unittest

from weld._staleness import _cap_stale_sources  # noqa: E402


class CapStaleSourcesTest(unittest.TestCase):
    """Direct coverage of the ``stale_sources`` bound.

    The diverging set can be the whole inventory after a rebase (ADR 0082),
    so it is capped, sorted for a deterministic total order, and the elided
    count is always reported -- never a silent truncation.
    """

    def test_under_cap_is_unchanged_and_reports_no_omission(self) -> None:
        entries = [{"path": "b.py", "reason": "x"}, {"path": "a.py", "reason": "y"}]
        kept, omitted = _cap_stale_sources(entries)
        self.assertEqual(
            kept,
            [{"path": "a.py", "reason": "y"}, {"path": "b.py", "reason": "x"}],
        )
        self.assertEqual(omitted, 0)

    def test_over_cap_keeps_the_first_n_sorted_paths_and_counts_the_rest(
        self,
    ) -> None:
        from weld._staleness import MAX_STALE_SOURCES

        entries = [
            {"path": f"src/f{i:03d}.py", "reason": "x"}
            for i in reversed(range(MAX_STALE_SOURCES + 7))
        ]
        kept, omitted = _cap_stale_sources(entries)
        self.assertEqual(len(kept), MAX_STALE_SOURCES)
        self.assertEqual(omitted, 7)
        self.assertEqual(kept[0]["path"], "src/f000.py")
        self.assertEqual(kept[-1]["path"], f"src/f{MAX_STALE_SOURCES - 1:03d}.py")

    def test_empty_input_is_unchanged(self) -> None:
        self.assertEqual(_cap_stale_sources([]), ([], 0))


class StaleReasonVocabularyTest(unittest.TestCase):
    """The reason vocabulary is closed: exactly these four strings."""

    def test_all_reasons_has_exactly_the_four_documented_strings(self) -> None:
        from weld._stale_reasons import (
            ALL_REASONS,
            CHANGED_SINCE_DISCOVERY,
            CONTENT_DIFFERS,
            INGESTED_FILE_VANISHED,
            NEVER_INGESTED,
        )

        self.assertEqual(
            ALL_REASONS,
            {
                CHANGED_SINCE_DISCOVERY, CONTENT_DIFFERS,
                INGESTED_FILE_VANISHED, NEVER_INGESTED,
            },
        )
        self.assertEqual(len(ALL_REASONS), 4)


if __name__ == "__main__":
    unittest.main()
