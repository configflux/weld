"""Post-accept enrichment invocation for the first-run prompt.

Split from :mod:`weld._first_run_enrich` so each module stays under
the line-count cap and so the heavy imports
(:mod:`weld.enrich`, :mod:`weld.graph`) are only loaded on the
narrow code path that actually accepted the Branch A prompt.

The single public function, :func:`run_enrichment_on_accept`,
mirrors ``wd enrich --provider <name>`` for the detected provider.
Errors from the provider (auth, network, quota) are surfaced to
stderr but never bubble up: the just-written graph is already valid
without enrichment, so a failed enrich must not poison a successful
discover.
"""

from __future__ import annotations

from pathlib import Path
from weld._notice import emit


def run_enrichment_on_accept(root: Path, provider: str | None) -> None:
    """Invoke ``wd enrich`` for the user who accepted the prompt.

    A ``None`` *provider* (defensively guards against an evaluator
    misclassification) short-circuits silently. The enrich call is
    wrapped in a broad exception handler because the user accepted
    *enrichment*, not "abort discover" -- a quota error or transient
    network glitch should print and move on, not unwind the caller.
    """
    if provider is None:
        return
    from weld.enrich import enrich as _enrich
    from weld.graph import Graph

    graph_obj = Graph(root)
    graph_obj.load()
    try:
        _enrich(graph_obj, provider_name=provider, persist=True)
    except Exception as exc:  # noqa: BLE001 -- enrichment is opportunistic
        emit(
            f"[weld] enrichment after first-run prompt failed: {exc}"
        )
