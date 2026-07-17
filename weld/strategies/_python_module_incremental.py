"""Incremental-mode helper for the python_module strategy (ADR 0084).

``python_module.extract`` normally re-globs its directory pattern and
re-parses **every** sibling file to emit one ``file`` node per module. On a
warm incremental refresh that is pure waste: a 1-file edit re-parses ~220
siblings and the orchestrator then discards every node whose file is not
dirty. A warm-path profile measured this at ~370-470 ms per dirty glob on this
corpus -- material by the ADR 0074/0084 >~100 ms threshold, which is why this
helper exists (the measurement gate in bd ir2l).

This mirrors ``_python_callgraph_incremental`` but is strictly simpler:
``python_module.extract`` has **zero cross-file state** (every node property
is a pure function of one file's AST; it emits no origin-tagged cross-file
edges). So there is nothing to reconstruct from the prior graph -- narrowing
the parse loop to ``matched ∩ dirty_files`` is the whole optimization, and the
merged node set is byte-identical to a full parse's (ADR 0084 § Byte-safety).

When the orchestrator passes an :class:`IncrementalHint` (via the reserved
``context`` key), the strategy parses only the dirty subset of its glob.
``hint is None`` -- full discover and every non-incremental caller -- keeps
today's whole-glob behaviour byte-for-byte.
"""

from __future__ import annotations

from pathlib import Path

from weld.strategies._incremental_hint import dirty_matched, get_incremental_hint


def dirty_scoped_matched(
    matched: list[Path],
    root: Path,
    context: dict,
) -> list[Path]:
    """Return *matched*, narrowed to the dirty subset when a hint is present.

    Reads the incremental hint off *context*. When present, returns only the
    matched files whose repo-relative path is dirty (order preserved).
    When absent -- full discover or any non-incremental caller -- returns
    *matched* unchanged so the whole-glob path is byte-for-byte identical.
    """
    hint = get_incremental_hint(context)
    if hint is None:
        return matched
    return dirty_matched(matched, root, hint.dirty_files)


__all__ = ["dirty_scoped_matched"]
