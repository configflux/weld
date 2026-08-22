"""Closed reason vocabulary for ``compute_stale_info``'s ``stale_sources``.

Four strings, one per code path that can name a diverging file (ADR 0017
amendment). Defined once so
:mod:`weld._staleness`, :mod:`weld._staleness_worktree`, and
:mod:`weld._staleness_coverage` cannot drift on spelling, and so a test can
assert the vocabulary stays closed -- exactly these four strings and no
others -- without duplicating the literals.

Not a reason for every way ``source_stale`` can become ``True``. The "no
recorded ``git_sha``" and "``commits_behind == -1`` (unreachable history,
e.g. a force-push)" states have no computable file-level diff to name, so
they leave ``stale_sources: []`` and stay distinguished by the existing
top-level ``reason`` / ``graph_sha`` / ``commits_behind`` fields instead of
a fifth reason string here. Likewise the ADR 0101 "inventory cannot vouch
for the graph body at all" doubt (:func:`weld._staleness_coverage.
inventory_vouches_for_graph` returning ``False`` with no specific uncovered
file) has no single path to blame -- under-report rather than invent one.
"""

from __future__ import annotations

#: Committed diff: a tracked file changed between the recorded ``graph_sha``
#: and HEAD (:func:`weld._git.source_files_changed_since`).
CHANGED_SINCE_DISCOVERY = "changed since last discovery"

#: An ingested file's hash no longer matches the ADR 0008 inventory --
#: either the working-tree copy of a file git reports dirty
#: (:func:`weld._staleness_worktree.dirty_sources_diverge`), or an
#: inventoried file's *current* content checked directly, independent of
#: git's dirty view (:func:`weld._staleness_reverted.reverted_content_stale`,
#: ADR 0017 fourth amendment -- the edited-then-reverted case, where the tree
#: is clean but the graph still holds the edit).
CONTENT_DIFFERS = "content differs"

#: An ingested file the inventory has a hash for can no longer be read --
#: deleted, or replaced by something unreadable. Reached either through the
#: working tree's dirty list, or directly through the inventory when the
#: file was never staged and so left no trace in ``git status`` at all (see
#: :mod:`weld._staleness_reverted`).
INGESTED_FILE_VANISHED = "ingested file vanished"

#: In scope, never ingested: discovery would resolve this file today but the
#: inventory has no record of it -- either an uncommitted new file
#: (``dirty_sources_diverge``) or an ADR 0101 coverage gap
#: (:func:`weld._staleness_coverage.files_missing_from_inventory`). Same
#: underlying fact, reached by two different code paths.
NEVER_INGESTED = "in-scope file never ingested"

#: The closed set, for a test to assert against without hard-coding the
#: literals a second time.
ALL_REASONS = frozenset(
    {CHANGED_SINCE_DISCOVERY, CONTENT_DIFFERS, INGESTED_FILE_VANISHED, NEVER_INGESTED}
)
