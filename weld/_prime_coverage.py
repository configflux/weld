"""Description-coverage rendering for ``wd prime``.

Extracted from ``weld/prime.py`` so the main prime module stays under
the 400-line cap (see AGENTS.md / CLAUDE.md line-count policy). The
rendering is intentionally separate from the graph-side computation in
:mod:`weld._graph_stats` so each layer has a single responsibility:

- :func:`weld._graph_stats.compute_meaningful_coverage` knows the
  numerical contract (which types count, how to score them, and the
  cost-estimate fields).
- :func:`describe_meaningful_coverage` here translates that payload
  into the ``[ACTION]`` / ``[INFO]`` / silent triage that ``wd prime``
  uses to drive operator behaviour.

The escalation threshold lives here because it is a UX choice -- below
this line the product is missing-coverage-on-actionable-types and the
user should be nudged to run ``wd enrich``; above it, the gap is small
enough to be informational only. Reframing the headline metric without
this UX rule would still leave ``wd prime`` saying ``[INFO] 1% have
descriptions`` on a 100%-covered repo.
"""

from __future__ import annotations

from typing import Callable, Mapping

from weld._graph_stats import (
    MEANINGFUL_DESCRIPTION_TYPES,
    compute_meaningful_coverage,
)

# Threshold (percent) below which meaningful description coverage is
# treated as actionable rather than informational. Above this line, the
# product is in good shape and ``wd prime`` stays quiet (or emits a
# soft INFO when a noticeable gap remains). Below it, we escalate to
# [ACTION] with a concrete next-command so users do not have to invent
# the right ``wd enrich`` invocation themselves.
MEANINGFUL_COVERAGE_THRESHOLD = 80.0

StatusFn = Callable[[str, str], str]
ActionFn = Callable[[str, str], tuple[str, str]]


def describe_meaningful_coverage(
    nodes: Mapping[str, dict],
    *,
    status: StatusFn,
    action: ActionFn,
    threshold: float = MEANINGFUL_COVERAGE_THRESHOLD,
) -> tuple[list[str], list[str]]:
    """Return ``(lines, steps)`` for the description-coverage block.

    ``status`` and ``action`` are the same formatters ``wd prime`` uses
    for every other check -- passing them in keeps the prefix layout
    (``[INFO]`` / ``[ACTION]`` / ``-> Run: ...``) consistent without
    importing ``prime`` from this helper (which would introduce a
    circular import).

    Output rules:

    - Below ``threshold``: emit ``[ACTION]`` with a
      ``wd enrich --types=<missing-types>`` next-command.
    - Otherwise, when there are still uncovered candidates: emit a soft
      ``[INFO]`` advisory so the operator sees the gap without being
      told to act on it.
    - When meaningful coverage is at 100% (or there are no candidate
      nodes at all): no line is emitted -- silence is success.
    """
    nc, dc = _count_meaningful_nodes(nodes)
    payload = compute_meaningful_coverage(nc, dc)
    if payload["total"] == 0:
        return [], []

    pct = float(payload["coverage_pct"])
    missing = int(payload["candidates_missing"])
    if missing == 0:
        return [], []

    missing_types = sorted(
        t for t in MEANINGFUL_DESCRIPTION_TYPES
        if nc.get(t, 0) > dc.get(t, 0)
    )
    summary = (
        f"{_format_pct(pct)}% of meaningful nodes have descriptions "
        f"({missing} candidate{'s' if missing != 1 else ''} missing)"
    )

    if pct < threshold:
        types_arg = ",".join(missing_types) if missing_types else ""
        cmd = f"wd enrich --types={types_arg}" if types_arg else "wd enrich"
        line, step = action(summary, cmd)
        return [line], [step]
    return [status("INFO", summary)], []


def _count_meaningful_nodes(
    nodes: Mapping[str, dict],
) -> tuple[dict[str, int], dict[str, int]]:
    """Return ``(nodes_by_type, described_by_type)`` for *nodes*.

    The same per-type bookkeeping is done by
    :func:`weld._graph_stats.compute_stats` over the full graph; here
    we replay it on the prime-side dict so ``wd prime`` can render the
    coverage line without loading the full :class:`weld.graph.Graph`
    object (it already has the parsed JSON in hand).
    """
    nc: dict[str, int] = {}
    dc: dict[str, int] = {}
    for node in nodes.values():
        if not isinstance(node, dict):
            continue
        node_type = node.get("type")
        if not isinstance(node_type, str):
            continue
        nc[node_type] = nc.get(node_type, 0) + 1
        props = node.get("props") or {}
        desc = props.get("description")
        if isinstance(desc, str) and desc.strip():
            dc[node_type] = dc.get(node_type, 0) + 1
    return nc, dc


def _format_pct(pct: float) -> str:
    """Render *pct* as a compact human-readable percent.

    Whole numbers drop the decimal (``80`` not ``80.0``) so the
    one-line summary in ``wd prime`` stays readable; fractional values
    keep one decimal so ``79.9`` does not silently round up to ``80``
    and cross the threshold visually.
    """
    if abs(pct - round(pct)) < 1e-6:
        return f"{int(round(pct))}"
    return f"{pct:.1f}"
