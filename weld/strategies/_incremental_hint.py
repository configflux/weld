"""Typed incremental hint shared between the orchestrator and strategies.

ADR 0074 routes a dirty-file scope to incremental-aware strategies
(``python_callgraph``) without breaking the ``extract(root, source,
context)`` contract or putting the hint on the declarative ``source`` dict.
The orchestrator's ``run_source`` deposits an :class:`IncrementalHint` under
``context[INCREMENTAL_HINT_KEY]`` for the duration of the strategy call.

This type lives in the **strategies** package (the lower layer) so that both
the strategy and the orchestrator's ``_discover_strategies`` (runtime layer,
which already imports ``StrategyResult`` from this package) can reference it
without a circular dependency: runtime depends on strategies, never the
reverse.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Reserved ``context`` key carrying the incremental hint into ``extract``.
INCREMENTAL_HINT_KEY = "_incremental_hint"


@dataclass(frozen=True)
class IncrementalHint:
    """Typed dirty-file scope handed to incremental-aware strategies.

    Carries the repo-relative ``dirty_files`` set and the orchestrator's
    **post-purge** prior node set (``prior_nodes``) so a strategy can
    re-extract only the dirty files and reconstruct cross-file state (e.g.
    ``python_callgraph``'s ``project_modules``) from surviving prior nodes
    instead of re-globbing siblings. Only ``python_callgraph`` consults it;
    every other strategy ignores it and is unchanged.
    """

    dirty_files: frozenset[str] = field(default_factory=frozenset)
    prior_nodes: dict[str, dict] = field(default_factory=dict)


__all__ = ["INCREMENTAL_HINT_KEY", "IncrementalHint"]
