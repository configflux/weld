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


# The freshness oracle (:mod:`weld._federation_staleness`) emits exactly six
# child states. ``fresh`` and ``stale`` are the two a child that is actually
# checked out on disk can hold -- ``stale`` is an on-disk child whose graph
# drifted (ADR 0066 §2). ``missing`` / ``uninitialized`` / ``corrupt`` /
# ``unknown`` mean the child is absent or unreadable, so it cannot be vouched
# for. There is deliberately no ``present`` state here: ``present`` is the
# ``wd workspace status`` spelling of "on disk", and counting it in this
# module is what made the roster report ``present`` == ``stale`` while every
# fresh child landed in no bucket at all. The absent order mirrors
# ``weld.workspace_state._STATUS_ORDER`` so the two surfaces read alike.
_ON_DISK_CHILD_STATES: tuple[str, ...] = ("fresh", "stale")
_ABSENT_CHILD_ORDER: tuple[str, ...] = ("missing", "uninitialized", "corrupt", "unknown")


def child_roster_lines(children: list[Any]) -> list[str]:
    """Render the federated child roster for ``wd stale`` (bd 51oxx).

    The old form ``children: N (0 stale)`` collapsed the ADR 0134
    cannot-answer distinction: at a federation root with zero children
    checked out on disk every child is ``missing``, so ``0 stale`` read as
    "all healthy" when in fact none exist to be stale. This summary reports
    how many of the registered children are actually **present** on disk
    (``fresh`` plus ``stale``), how many of those have drifted, and a
    per-state breakdown of the absent ones -- reusing the ``wd workspace
    status`` lifecycle vocabulary so registered-but-absent is never mistaken
    for present-and-fresh. ``stale`` is a *sub-count* of ``present``, not a
    bucket that empties it: a child whose graph drifted is still checked out.
    ``wd workspace status`` counts the same way, so the two commands never
    disagree about how many children are on disk. Stale children are still
    enumerated one per line below the summary.

    Children are bucketed by the vocabulary the oracle speaks; a state named
    by neither list is still rendered under its own name, so the arithmetic
    conserves (``registered == present + sum(absent)``) and a future state
    cannot silently vanish the way ``fresh`` once did.
    """
    counts = Counter(
        str(c.get("state")) for c in children if isinstance(c, Mapping)
    )
    stale = counts.get("stale", 0)
    present = sum(counts.get(state, 0) for state in _ON_DISK_CHILD_STATES)
    summary = (
        f"  children: {len(children)} registered, "
        f"{present} present, {stale} stale"
    )
    unrecognized = sorted(
        set(counts) - set(_ON_DISK_CHILD_STATES) - set(_ABSENT_CHILD_ORDER),
    )
    absent = [
        f"{state}={counts[state]}"
        for state in (*_ABSENT_CHILD_ORDER, *unrecognized)
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


def stats_workspace_lines(workspaces: Mapping[str, Any]) -> list[str]:
    """Render ``wd stats``' workspace block, or ``[]`` at a single repo.

    The summary splits *registered* from *present* for the reason
    :func:`child_roster_lines` does: the old bare form (``workspaces: 2
    children``) is a registered count that reads as a presence claim, which
    is exactly how ``children: 4 (0 stale)`` came to be read as "all
    healthy" at a root where none were checked out. Present means the same
    thing here as on the other two surfaces -- lifecycle ``present``, i.e.
    on disk -- so the three numbers are comparable by construction.

    A single pointer line follows, and only when the stored ledger and the
    disk disagree. It is one line rather than the per-child block
    ``wd workspace status`` prints, because this is the summary surface and
    that is the detail one; naming it hands the reader the remedy too,
    since that command's own drift block ends in ``run: wd discover``.
    """
    if not workspaces:
        return []
    lines = [
        f"  workspaces: {workspaces.get('count', 0)} registered, "
        f"{workspaces.get('present', 0)} present",
    ]
    drifted = workspaces.get("drift_count") or 0
    if isinstance(drifted, int) and drifted > 0:
        noun = "child differs" if drifted == 1 else "children differ"
        lines.append(
            f"  workspace ledger drift: {drifted} {noun} from the stored "
            "ledger -- run wd workspace status for detail",
        )
    return lines
