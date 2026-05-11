"""Consolidated C++ post-extraction pass for the tree-sitter strategy.

The per-file tree-sitter walk in :mod:`weld.strategies.tree_sitter`
emits ``symbol:unresolved:<name>`` sentinels for every call that
crosses a translation-unit boundary and stops there. Three post-passes
have to run in order before the C++ extraction is complete:

  1. ``augment_state_with_headers`` walks the repo for ``.h``/``.hpp``/
     ``.hxx`` files the configured glob did not cover and appends them
     to the per-file index so the include resolver has a complete
     symbol view.
  2. ``resolve_includes_pass`` rewrites unresolved sentinels against
     project headers each impl ``#include``s.
  3. ``emit_header_source_pairs`` (ADR 0057 Wave 2) surfaces the
     header/source pairing the resolver discovers internally as
     ``file:<header>  --implemented_by-->  file:<source>`` graph edges.

This module is the orchestration helper that keeps the strategy module
under the 400-line cap (it would otherwise carry seven inline glue
lines per language plus its own logic).
"""

from __future__ import annotations

from pathlib import Path

from weld.strategies.cpp_resolver import (
    augment_state_with_headers,
    emit_header_source_pairs,
    resolve_includes_pass,
)


def run_cpp_post_pass(
    root: Path,
    per_file: list[dict],
    nodes: dict[str, dict],
    edges: list[dict],
    language: str,
    excludes: list,
    parse_symbols,
    source_strategy: str = "tree_sitter",
) -> None:
    """Run the layer-2 + ADR 0057 Wave 2 passes for C++ extraction.

    The arguments mirror the layer-2 helpers so this orchestrator is a
    drop-in replacement for the three inline calls it consolidates.
    Mutates *per_file*, *nodes*, and *edges* in place.

    Args:
        root: Repository root used as the header-walk anchor and the
            ADR 0042 origin-classifier reference point.
        per_file: Per-file state list assembled by the strategy. The
            header walk appends new entries here.
        nodes: Graph node dict. The include resolver mints resolved
            symbol nodes; the pairing helper never adds nodes.
        edges: Graph edge list. The include resolver rewrites existing
            unresolved edges; the pairing helper appends new
            ``implemented_by`` edges.
        language: Always ``"cpp"`` for the current call site; kept as
            an explicit argument so the helper has no hard dep on a
            literal.
        excludes: Source-entry exclude globs honoured by the header
            walk.
        parse_symbols: Callable matching the
            ``_parse_file_symbols(file_path, language, queries)`` shape
            from :mod:`weld.strategies.tree_sitter`. Bound by the
            strategy so this orchestrator has no import-time dep on
            tree-sitter.
        source_strategy: ``props.source_strategy`` stamp on the
            ``implemented_by`` edges. Defaults to ``"tree_sitter"``
            because that is the strategy that drives the entire pass.
    """
    augment_state_with_headers(
        root, per_file, language, excludes, parse_symbols,
    )
    resolve_includes_pass(root, per_file, nodes, edges)
    emit_header_source_pairs(
        per_file, edges, source_strategy=source_strategy,
    )


__all__ = ["run_cpp_post_pass"]
