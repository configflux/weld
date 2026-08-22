"""Resolve ``.weld/discover.yaml`` glob-declaring source entries to file sets.

Split out of :mod:`weld._graph_strategy_pair` so the per-**entry**
resolution :mod:`weld._graph_edge_provenance_lint` needs does not duplicate
the glob-walking logic that module already implements. The distinction
matters: :func:`strategy_file_sets` merges every entry sharing one
``strategy:`` name into a single bucket (what strategy-pair-consistency
wants -- "the whole file set this strategy would visit"), while
:func:`source_entry_file_sets` keeps each literal YAML list entry separate
(what cross-source-edge-provenance wants -- "would dirtying file B force
*this specific* extract() call, the one that also read file A, to re-run").
A strategy registered on several disjoint globs (``python_callgraph`` has
four) is one strategy but several source entries, and only the entry
granularity tells the two files apart correctly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence


def source_entry_file_sets(
    root: Path, sources: Sequence[Mapping]
) -> list[tuple[str, set[str]]]:
    """Return ``[(strategy, file_set), ...]``, one tuple per glob entry.

    Walks each ``glob:`` source entry under *root* with the same
    prune-aware walker the strategies themselves use, then applies that
    entry's ``exclude:`` list. Entries are kept in declaration order and
    are **not** merged across entries that share a ``strategy:`` name --
    see the module docstring for why that distinction is load-bearing.
    Sources without a ``glob`` (``files:`` / ``path:`` entries) are
    skipped: a literal-path entry names at most one file, so it can never
    contribute a *multi*-file same-entry exemption that the simpler
    from-file-equals-to-file check does not already cover.
    """
    from weld.glob_match import walk_glob

    entries: list[tuple[str, set[str]]] = []
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        strategy = source.get("strategy")
        glob_pattern = source.get("glob")
        if not strategy or not glob_pattern:
            continue
        excludes = source.get("exclude") or []
        if not isinstance(excludes, list):
            excludes = []
        try:
            matched = walk_glob(root, str(glob_pattern), excludes=excludes)
        except (OSError, ValueError):
            matched = []
        file_set: set[str] = set()
        for path in matched:
            try:
                file_set.add(path.relative_to(root).as_posix())
            except ValueError:
                continue
        entries.append((str(strategy), file_set))
    return entries


def strategy_file_sets(
    root: Path, sources: Sequence[Mapping]
) -> dict[str, set[str]]:
    """Return ``{strategy_name: {rel_posix_path, ...}}``, merged across entries.

    The file set each strategy *would* visit on the current tree, before
    any per-strategy ``should_skip`` logic the strategy applies
    internally -- a thin union of :func:`source_entry_file_sets` over every
    entry declaring that strategy, preserved for
    :mod:`weld._graph_strategy_pair`'s declared/emitted comparison, which
    legitimately wants the whole-strategy set rather than one entry at a
    time.
    """
    by_strategy: dict[str, set[str]] = {}
    for strategy, file_set in source_entry_file_sets(root, sources):
        by_strategy.setdefault(strategy, set()).update(file_set)
    return by_strategy


def declared_strategies(sources: Sequence[Mapping]) -> set[str]:
    """Return every ``strategy:`` name appearing in *sources*, any entry form.

    Unlike the two functions above, this is not restricted to ``glob:``
    entries -- a ``path:`` strategy (``concept_from_bd``) or a ``files:``
    strategy is just as much a real, discover.yaml-registered strategy as
    a glob one, and :mod:`weld._graph_edge_provenance_lint` needs the full
    set to tell a real strategy's edge apart from a post-processing
    synthesis pass (``graph_closure``, ``topology``) that never appears in
    ``sources:`` at all and re-runs in full on every discovery, so it is
    never at ADR 0074 purge risk regardless of provenance.
    """
    names: set[str] = set()
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        strategy = source.get("strategy")
        if strategy:
            names.add(str(strategy))
    return names


__all__ = [
    "declared_strategies",
    "source_entry_file_sets",
    "strategy_file_sets",
]
