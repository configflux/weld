"""Full-enumeration companion to the dirty-worktree divergence predicate.

Sibling to ``weld_dirty_worktree_settles_test`` (shares its fixture via
``weld.tests._dirty_tree_lib``, split out only to keep both files under the
line-count cap): that suite pins the boolean gate
(``weld._staleness_worktree.dirty_sources_diverge``) and the settle
end-to-end; this one pins its full-enumeration companion,
``dirty_sources_diverge_detail``, added so a stale verdict can name every
diverging path and why instead of just the first one.
"""

from __future__ import annotations

import unittest

from weld._stale_reasons import (  # noqa: E402
    CONTENT_DIFFERS,
    INGESTED_FILE_VANISHED,
    NEVER_INGESTED,
)
from weld._staleness_worktree import (  # noqa: E402
    dirty_sources_diverge,
    dirty_sources_diverge_detail,
)
from weld.tests._dirty_tree_lib import DirtyTreeFixture  # noqa: E402


class DirtySourcesDivergeDetailUnitTest(DirtyTreeFixture):
    """Direct coverage of the full-enumeration companion.

    Mirrors ``DirtySourcesDivergeUnitTest`` (in the sibling settle suite)
    case for case, but asserts the reason-tagged detail instead of the
    boolean gate.
    """

    def test_empty_dirty_set_returns_nothing(self) -> None:
        self.assertEqual(dirty_sources_diverge_detail(self.root, []), [])

    def test_unchanged_ingested_file_returns_nothing(self) -> None:
        self.assertEqual(
            dirty_sources_diverge_detail(self.root, ["src/a.py"]), [],
        )

    def test_changed_ingested_file_reports_content_differs(self) -> None:
        (self.root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
        self.assertEqual(
            dirty_sources_diverge_detail(self.root, ["src/a.py"]),
            [{"path": "src/a.py", "reason": CONTENT_DIFFERS}],
        )

    def test_vanished_ingested_file_reports_vanished(self) -> None:
        (self.root / "src" / "a.py").unlink()
        self.assertEqual(
            dirty_sources_diverge_detail(self.root, ["src/a.py"]),
            [{"path": "src/a.py", "reason": INGESTED_FILE_VANISHED}],
        )

    def test_unknown_in_scope_file_reports_never_ingested(self) -> None:
        (self.root / "src" / "c.py").write_text("c = 1\n", encoding="utf-8")
        self.assertEqual(
            dirty_sources_diverge_detail(self.root, ["src/c.py"]),
            [{"path": "src/c.py", "reason": NEVER_INGESTED}],
        )

    def test_unknown_path_not_on_disk_reports_nothing(self) -> None:
        self.assertEqual(
            dirty_sources_diverge_detail(self.root, ["src/gone.py"]), [],
        )

    def test_unknown_out_of_scope_file_reports_nothing(self) -> None:
        (self.root / "scratch.log").write_text("x\n", encoding="utf-8")
        self.assertEqual(
            dirty_sources_diverge_detail(self.root, ["scratch.log"]), [],
        )

    def test_every_divergence_is_reported_not_just_the_first(self) -> None:
        # The point of full enumeration: dirty_sources_diverge stops at the
        # first divergence in list order ("src/a.py"); this must name all
        # three, proving the short-circuit in the bool gate is not inherited.
        (self.root / "src" / "a.py").write_text("a = 9\n", encoding="utf-8")
        (self.root / "src" / "b.py").write_text("b = 9\n", encoding="utf-8")
        (self.root / "src" / "c.py").write_text("c = 1\n", encoding="utf-8")
        result = dirty_sources_diverge_detail(
            self.root, ["src/a.py", "notes.txt", "src/b.py", "src/c.py"],
        )
        self.assertEqual(
            {(e["path"], e["reason"]) for e in result},
            {
                ("src/a.py", CONTENT_DIFFERS),
                ("src/b.py", CONTENT_DIFFERS),
                ("src/c.py", NEVER_INGESTED),
            },
        )

    def test_missing_inventory_reports_nothing_but_gate_stays_conservative(
        self,
    ) -> None:
        (self.root / ".weld" / "discovery-state.json").unlink()
        self.assertEqual(
            dirty_sources_diverge_detail(self.root, ["src/a.py"]), [],
        )
        # Under-report detail rather than invent a path-level claim the
        # undecidable inputs cannot back; the boolean gate is unchanged.
        self.assertTrue(dirty_sources_diverge(self.root, ["src/a.py"]))


if __name__ == "__main__":
    unittest.main()
