"""Shared checkouts for the MCP per-request root suites (ADR 0096 §4).

Both suites ask the same question of a running server -- *which checkout
answered* -- so both need real sibling checkouts that disagree about their
contents. That fixture is built once here and rides in the ``srcs`` of each
target (the ``_impact_test_helpers`` pattern), on top of the Mode B
repository the seeding suites already define in :mod:`_mode_b_fixture`.

The split itself is by subject: :mod:`weld_mcp_request_root_test` covers
what the argument *does* (answer from the named checkout, seed it, record
it), :mod:`weld_mcp_root_bound_test` covers what it may not do.
"""

from __future__ import annotations

import os
from pathlib import Path

from weld._mcp_read import clear_graph_cache
from weld.tests._mode_b_fixture import ModeBFixture, git

#: Tools that take a root: every graph-backed read.
ROOT_TOOLS = frozenset(
    {
        "weld_query", "weld_find", "weld_context", "weld_path", "weld_brief",
        "weld_stale", "weld_callers", "weld_references", "weld_export",
        "weld_diff", "weld_trace", "weld_impact",
    }
)

#: Tools that mutate the graph and must never be pointed at a checkout the
#: operator did not launch the server against.
ROOTLESS_TOOLS = frozenset({"weld_enrich", "weld_review"})


def match_ids(payload: dict) -> set[str]:
    """Match ids in a ``weld_query`` envelope -- "whose graph answered"."""
    return {m["id"] for m in payload.get("matches", [])}


class FrozenRefreshFixture(ModeBFixture):
    """Mode B checkouts with auto-refresh frozen, so content is the signal.

    ``WELD_AUTO_REFRESH=0`` stops a read from rewriting any graph mid-test.
    It also disables seeding (gate 1), which is exactly what the seeding
    test re-enables by extending :class:`ModeBFixture` directly instead.
    """

    def setUp(self) -> None:
        super().setUp()
        self._prev_refresh = os.environ.get("WELD_AUTO_REFRESH")
        os.environ["WELD_AUTO_REFRESH"] = "0"
        self.addCleanup(self._restore_refresh)
        clear_graph_cache()
        self.addCleanup(clear_graph_cache)

    def _restore_refresh(self) -> None:
        if self._prev_refresh is None:
            os.environ.pop("WELD_AUTO_REFRESH", None)
        else:
            os.environ["WELD_AUTO_REFRESH"] = self._prev_refresh

    def branch_worktree(self, term: str = "gamma") -> Path:
        """A linked worktree whose own graph knows a symbol the origin lacks."""
        from weld.tests._seed_fixture import discover

        worktree = self.worktree()
        (worktree / f"{term}.py").write_text(
            f"def {term}():\n    return 3\n", encoding="utf-8",
        )
        git(worktree, "add", f"{term}.py")
        git(worktree, "commit", "-q", "-m", f"branch adds {term}")
        discover(worktree)
        return worktree
