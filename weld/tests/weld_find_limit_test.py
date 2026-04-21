"""Behavioral regression for ``weld.file_index.find_files`` limit + score.

Spec:

* ``find_files(index, term)`` must emit an integer ``score`` on every file
  entry equal to the number of matching tokens.
* ``find_files(index, term, limit=N)`` must slice the result to at most
  ``N`` entries *after* ranking, without re-ordering.
* Existing ranking (matching-token count desc, path asc) must be preserved
  so adding ``score`` is purely additive for external consumers.

The test constructs an in-memory index so the assertions are independent
of disk layout, Bazel sandboxing, and any fixture drift.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.file_index import find_files  # noqa: E402


def _index_with_store_token_variants() -> dict[str, list[str]]:
    """Return an index with a deliberate spread of ``store`` hits per path.

    The number of matching tokens per path is:

    * ``app/store/index.py`` -> 4 (``Store``, ``store``, ``storeFactory``, ``StoreError``)
    * ``app/checkout/store.py`` -> 2 (``store``, ``Store``)
    * ``lib/b_store.py`` -> 1 (``b_store``)
    * ``lib/a_store.py`` -> 1 (``a_store``)
    * ``lib/z_store.py`` -> 1 (``z_store``)
    * ``docs/readme.md`` -> 0 (no match) -- must be excluded from results
    """
    return {
        "docs/readme.md": ["intro", "usage"],
        "lib/z_store.py": ["z_store", "helper"],
        "lib/a_store.py": ["a_store", "helper"],
        "lib/b_store.py": ["b_store", "helper"],
        "app/checkout/store.py": ["store", "Store", "cart", "total"],
        "app/store/index.py": [
            "Store", "store", "storeFactory", "StoreError", "unrelated",
        ],
    }


class FindFilesScoreTest(unittest.TestCase):
    """Every returned file entry must carry an integer ``score`` equal to
    ``len(matching_tokens)``.
    """

    def test_each_entry_has_integer_score_matching_token_count(self) -> None:
        idx = _index_with_store_token_variants()
        result = find_files(idx, "store")
        files = result["files"]
        self.assertTrue(files, "expected at least one matching file")
        for entry in files:
            self.assertIn("score", entry, f"missing score in {entry!r}")
            self.assertIsInstance(
                entry["score"], int,
                f"score must be int, got {type(entry['score']).__name__}",
            )
            self.assertEqual(
                entry["score"], len(entry["tokens"]),
                f"score {entry['score']} != len(tokens) for {entry['path']}",
            )


class FindFilesLimitTest(unittest.TestCase):
    """``limit`` must cap the returned file list; None / missing leaves the
    pre-change behaviour intact.
    """

    def test_limit_caps_results_to_requested_count(self) -> None:
        idx = _index_with_store_token_variants()
        result = find_files(idx, "store", limit=3)
        self.assertEqual(
            len(result["files"]), 3,
            f"expected 3 entries with limit=3, got {len(result['files'])}",
        )

    def test_limit_none_is_unchanged_from_default(self) -> None:
        """Passing ``limit=None`` must be indistinguishable from omitting it."""
        idx = _index_with_store_token_variants()
        a = find_files(idx, "store")
        b = find_files(idx, "store", limit=None)
        self.assertEqual(a, b)

    def test_limit_zero_yields_empty_files_list(self) -> None:
        """A limit of 0 is a legitimate request for zero results."""
        idx = _index_with_store_token_variants()
        result = find_files(idx, "store", limit=0)
        self.assertEqual(result["files"], [])

    def test_limit_larger_than_results_is_noop(self) -> None:
        """A limit bigger than the hit count must return every hit."""
        idx = _index_with_store_token_variants()
        unlimited = find_files(idx, "store")
        limited = find_files(idx, "store", limit=999)
        self.assertEqual(limited, unlimited)


class FindFilesOrderingTest(unittest.TestCase):
    """Adding ``score`` and ``limit`` must not perturb the historical
    ordering contract: matching-token count desc, then path asc.
    """

    def test_ordering_is_token_count_desc_then_path_asc(self) -> None:
        idx = _index_with_store_token_variants()
        result = find_files(idx, "store")
        ordered_paths = [f["path"] for f in result["files"]]
        # Expected ordering:
        #   app/store/index.py (4)
        #   app/checkout/store.py (2)
        #   lib/a_store.py (1)
        #   lib/b_store.py (1)
        #   lib/z_store.py (1)
        self.assertEqual(
            ordered_paths,
            [
                "app/store/index.py",
                "app/checkout/store.py",
                "lib/a_store.py",
                "lib/b_store.py",
                "lib/z_store.py",
            ],
        )

    def test_limit_preserves_top_of_ranked_order(self) -> None:
        idx = _index_with_store_token_variants()
        result = find_files(idx, "store", limit=2)
        self.assertEqual(
            [f["path"] for f in result["files"]],
            ["app/store/index.py", "app/checkout/store.py"],
        )


if __name__ == "__main__":
    unittest.main()
