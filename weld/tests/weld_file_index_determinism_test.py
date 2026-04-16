"""Determinism regression for ``weld/file_index.py::save_file_index``.

ADR 0012 scopes the determinism contract to ``graph.json``, but
``file-index.json`` is a sibling artifact emitted into the same
``.weld/`` directory, consumed by the same audience, and rides the same
diff-review workflow. This regression extends the contract to that
artifact.

The test constructs an envelope with unsorted top-level keys, unsorted
nested keys inside ``meta``, and unsorted per-file token lists, then
writes it twice via ``save_file_index`` and asserts:

1. The two written files are byte-identical (two-run stability).
2. The emitted text is byte-identical to a
   ``json.dumps(..., sort_keys=True)`` re-serialization of the parsed
   output (the canonical guard mirroring ``weld_determinism_dict_order_test``).
3. Per-file token lists are sorted lexicographically.

Mirrors the canonical guard used by
``weld/tests/weld_determinism_dict_order_test.py`` but scoped to
``file-index.json``.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.file_index import save_file_index  # noqa: E402


def _unsorted_index() -> dict[str, list[str]]:
    """Build an index whose keys and per-file token lists are in
    non-sorted insertion order so the sort assertions have something
    real to bite on.
    """
    return {
        "z/last.py": ["zeta", "alpha", "mu"],
        "a/first.py": ["omega", "beta", "gamma"],
        "m/middle.py": ["psi", "delta"],
    }


class FileIndexDeterminismTest(unittest.TestCase):
    """``save_file_index`` must emit canonical, byte-stable output."""

    def _emit_once(self, root: Path, index: dict[str, list[str]]) -> bytes:
        out = save_file_index(root, index)
        return out.read_bytes()

    def test_two_writes_byte_identical(self) -> None:
        """Calling ``save_file_index`` twice on the same input must
        produce byte-identical output.
        """
        index = _unsorted_index()
        with tempfile.TemporaryDirectory() as tda, \
                tempfile.TemporaryDirectory() as tdb:
            a = self._emit_once(Path(tda), index)
            b = self._emit_once(Path(tdb), index)
        self.assertEqual(
            a, b,
            "save_file_index must produce byte-identical output across "
            "two runs on the same input. Fix: route the write through "
            "sort_keys=True and sort per-file token lists.",
        )

    def test_output_is_sort_keys_canonical(self) -> None:
        """The emitted JSON must be byte-identical to a re-serialization
        with ``sort_keys=True`` at every level.
        """
        index = _unsorted_index()
        with tempfile.TemporaryDirectory() as td:
            raw = self._emit_once(Path(td), index)
        parsed = json.loads(raw.decode("utf-8"))
        resorted = json.dumps(parsed, indent=2, ensure_ascii=False, sort_keys=True)
        # save_file_index appends a single trailing newline.
        self.assertEqual(
            raw.decode("utf-8").rstrip("\n"),
            resorted,
            "file-index.json must emit every dict with sorted keys at "
            "every level of nesting (ADR 0012 §3 rule 4).",
        )

    def test_tokens_sorted_within_each_file_entry(self) -> None:
        """Per-file token lists must be emitted in sorted order so list
        content is stable across runs regardless of AST walk order.
        """
        index = _unsorted_index()
        with tempfile.TemporaryDirectory() as td:
            out = save_file_index(Path(td), index)
            data = json.loads(out.read_text(encoding="utf-8"))
        files = data["files"]
        for path, tokens in files.items():
            self.assertEqual(
                tokens, sorted(tokens),
                f"tokens for {path!r} are not sorted: {tokens!r}",
            )

    def test_trailing_newline_exactly_one(self) -> None:
        """The file must end with exactly one trailing newline, matching
        the canonical ``graph.json`` contract (ADR 0012 §3 rule 6).
        """
        with tempfile.TemporaryDirectory() as td:
            out = save_file_index(Path(td), _unsorted_index())
            text = out.read_text(encoding="utf-8")
        self.assertTrue(text.endswith("\n"))
        self.assertFalse(text.endswith("\n\n"))

    def test_roundtrip_load_unchanged(self) -> None:
        """Canonicalisation must not change the content read back by
        ``load_file_index``; consumers see the same mapping.
        """
        from weld.file_index import load_file_index

        index = _unsorted_index()
        with tempfile.TemporaryDirectory() as td:
            save_file_index(Path(td), index)
            loaded = load_file_index(Path(td))
        # Values may be reordered (sorted), but key set and multiset of
        # tokens per file must round-trip.
        self.assertEqual(set(loaded.keys()), set(index.keys()))
        for path, tokens in index.items():
            self.assertEqual(sorted(loaded[path]), sorted(tokens))


if __name__ == "__main__":
    unittest.main()
