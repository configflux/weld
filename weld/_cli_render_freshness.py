"""``wd stale`` diverging-source rendering.

Split out of :mod:`weld._cli_render` so that module stays under its
line-count cap, mirroring the existing :mod:`weld._cli_render_trust` /
:mod:`weld._cli_render_prose` pattern: return raw ``list[str]`` line
fragments that :func:`weld._cli_render.render_stale` merges into its own
``lines`` before the single ``_header``/``_join`` pass, rather than a
second rendered block competing with the first.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping


def stale_sources_lines(payload: Mapping[str, Any]) -> list[str]:
    """Render the ``stale_sources`` block for ``wd stale``'s human output.

    ``[]`` when the payload carries no diverging paths -- a fresh graph, or
    a stale one whose reason has no file-level detail to name (see
    :mod:`weld._stale_reasons`). Otherwise one line per path naming *why* it
    diverged, plus an elision note when the list was capped
    (``stale_sources_omitted``, ADR 0082's bounded-envelope rule: never
    silent-truncate).
    """
    sources = payload.get("stale_sources")
    if not isinstance(sources, list) or not sources:
        return []
    lines = [f"  stale_sources ({len(sources)}):"]
    for entry in sources:
        if isinstance(entry, Mapping):
            lines.append(f"    {entry.get('path')}: {entry.get('reason')}")
    omitted = payload.get("stale_sources_omitted")
    if isinstance(omitted, int) and omitted > 0:
        lines.append(f"    ... {omitted} more elided (capped)")
    return lines


def seed_blocked_lines(payload: Mapping[str, Any]) -> list[str]:
    """Render the optional ``seed_blocked_reason`` block (ADR 0100 amendment).

    ``[]`` for every payload that does not carry the key, which is all of them
    but one: a graphless linked worktree of a repository that never ships
    ``.weld/discover.yaml`` (:func:`weld._stale_payload.seed_block_detail`).

    The cause arrives already hard-wrapped -- it is prose, not a scalar, and
    :mod:`weld._worktree_seed` wraps it so an 80-column terminal cannot reflow
    it into one grey paragraph. So it is emitted as its own ``key:`` heading
    with the lines indented beneath, the shape ``render_stats`` already uses
    for ``nodes_by_type``, rather than crammed onto a ``key: value`` line whose
    continuations would hang under the wrong column.
    """
    cause = payload.get("seed_blocked_reason")
    if not isinstance(cause, str) or not cause:
        return []
    return ["  seed_blocked_reason:"] + [
        f"    {line}" for line in cause.splitlines()
    ]


# ``present`` and ``stale`` are the two states a child that is actually
# checked out on disk can hold: ``stale`` is a present child whose graph
# drifted (ADR 0066 §2). Every other lifecycle state -- ``missing`` /
# ``uninitialized`` / ``corrupt`` / ``unknown`` -- means the child is absent
# or unreadable, so it cannot be vouched for. Order mirrors
# ``weld.workspace_state._STATUS_ORDER`` so the two surfaces read alike.
_ABSENT_CHILD_ORDER: tuple[str, ...] = ("missing", "uninitialized", "corrupt", "unknown")


def child_roster_lines(children: list[Any]) -> list[str]:
    """Render the federated child roster for ``wd stale`` (bd 51oxx).

    The old form ``children: N (0 stale)`` collapsed the ADR 0134
    cannot-answer distinction: at a federation root with zero children
    checked out on disk every child is ``missing``, so ``0 stale`` read as
    "all healthy" when in fact none exist to be stale. This summary reports
    how many of the registered children are actually **present** on disk
    (present or stale), how many are ``stale``, and a per-state breakdown of
    the absent ones -- reusing the ``wd workspace status`` lifecycle
    vocabulary so registered-but-absent is never mistaken for
    present-and-fresh. Stale children are still enumerated one per line
    below the summary.
    """
    counts = Counter(
        str(c.get("state")) for c in children if isinstance(c, Mapping)
    )
    stale = counts.get("stale", 0)
    present = counts.get("present", 0) + stale
    summary = (
        f"  children: {len(children)} registered, "
        f"{present} present, {stale} stale"
    )
    absent = [
        f"{state}={counts[state]}"
        for state in _ABSENT_CHILD_ORDER
        if counts.get(state)
    ]
    if absent:
        summary += f" ({', '.join(absent)})"
    lines = [summary]
    for child in children:
        if not isinstance(child, Mapping) or child.get("state") != "stale":
            continue
        behind = child.get("commits_behind")
        suffix = f", {behind} behind" if isinstance(behind, int) and behind > 0 else ""
        lines.append(
            f"    {child.get('name')}: stale ({child.get('reason')}{suffix})",
        )
    return lines
