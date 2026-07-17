"""Typed incremental hint shared between the orchestrator and strategies.

ADR 0074 routes a dirty-file scope to incremental-aware strategies without
breaking the ``extract(root, source, context)`` contract or putting the hint
on the declarative ``source`` dict. The orchestrator's ``run_source``
deposits an :class:`IncrementalHint` under ``context[INCREMENTAL_HINT_KEY]``
for the duration of the strategy call.

Two strategies consult the hint: ``python_callgraph`` (ADR 0074) and
``python_module`` (ADR 0084). This module owns the hint *type* plus the two
strategy-agnostic primitives every incremental-aware strategy needs -- read
the hint off ``context`` (:func:`get_incremental_hint`) and narrow a matched
file list to the dirty subset (:func:`dirty_matched`). Strategy-specific
reconstruction (e.g. ``python_callgraph``'s ``project_modules``) stays in the
per-strategy ``_*_incremental`` helper.

This module lives in the **strategies** package (the lower layer) so that both
the strategies and the orchestrator's ``_discover_strategies`` (runtime layer,
which already imports ``StrategyResult`` from this package) can reference it
without a circular dependency: runtime depends on strategies, never the
reverse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Reserved ``context`` key carrying the incremental hint into ``extract``.
INCREMENTAL_HINT_KEY = "_incremental_hint"


@dataclass(frozen=True)
class IncrementalHint:
    """Typed dirty-file scope handed to incremental-aware strategies.

    Carries the repo-relative ``dirty_files`` set and the orchestrator's
    **post-purge** prior node set (``prior_nodes``) so a strategy can
    re-extract only the dirty files and reconstruct cross-file state (e.g.
    ``python_callgraph``'s ``project_modules``) from surviving prior nodes
    instead of re-globbing siblings. ``python_callgraph`` (ADR 0074) and
    ``python_module`` (ADR 0084) consult it; every other strategy ignores it
    and is unchanged. ``python_module`` uses only ``dirty_files`` -- it has no
    cross-file state, so it never reads ``prior_nodes``.
    """

    dirty_files: frozenset[str] = field(default_factory=frozenset)
    prior_nodes: dict[str, dict] = field(default_factory=dict)


def get_incremental_hint(context: dict) -> IncrementalHint | None:
    """Return the typed incremental hint stashed in *context*, if any."""
    if not isinstance(context, dict):
        return None
    hint = context.get(INCREMENTAL_HINT_KEY)
    return hint if isinstance(hint, IncrementalHint) else None


def dirty_matched(
    matched: list[Path],
    root: Path,
    dirty_files: frozenset[str],
) -> list[Path]:
    """Restrict *matched* to files whose repo-relative path is dirty.

    Order is preserved from *matched* so the parse loop and any
    order-sensitive downstream behaviour are unchanged relative to the
    full-glob path for the surviving subset.
    """
    out: list[Path] = []
    for py in matched:
        try:
            rel = str(py.relative_to(root))
        except ValueError:
            continue
        if rel in dirty_files:
            out.append(py)
    return out


__all__ = [
    "INCREMENTAL_HINT_KEY",
    "IncrementalHint",
    "dirty_matched",
    "get_incremental_hint",
]
