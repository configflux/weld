"""Helpers for ``wd agents audit --strict``.

ADR 0029 silences ``duplicate_name`` and ``vague_description`` findings
when assets form a canonical->rendered pair. Strict mode surfaces those
silenced groups as ``info``-level findings so operators can verify the
canonical+rendered relationship is intentional.

The connectivity predicate ``all_render_linked`` is shared with
``weld.agent_graph_audit._duplicate_names`` so the suppression rule has
exactly one source of truth. The vague-description type filter and
word-bag live in ``weld._agent_graph_constants`` and are imported by
both this module and ``weld.agent_graph_audit`` so a future rule edit
only happens in one place.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

from weld._agent_graph_constants import (
    _CLEAR_DESCRIPTION_TYPES, _VAGUE_DESCRIPTIONS,
)


def suppressed_duplicate_findings(
    assets: list[dict[str, Any]],
    generated_links: dict[str, set[str]],
    *,
    finding_factory: Callable[..., dict[str, Any]],
    norm: Callable[[str], str],
) -> list[dict[str, Any]]:
    """Info-level findings for duplicate-name groups silenced by ADR 0029.

    Mirrors ``_duplicate_names`` but inverts the suppression: the group
    is reported only when the asset IDs form a single connected component
    under *generated_links* (the canonical+rendered case) -- the case
    that ``_duplicate_names`` would otherwise drop.
    """
    groups: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for asset in assets:
        groups[(asset["type"], norm(asset["name"]))].append(asset)
    findings: list[dict[str, Any]] = []
    for items in groups.values():
        if len(items) <= 1 or not all_render_linked(items, generated_links):
            continue
        findings.append(finding_factory(
            "duplicate_name_suppressed",
            "Suppressed duplicate name (canonical+rendered)",
            (
                f"Suppressed by ADR 0029: {len(items)} {items[0]['type']} "
                f"assets share name {items[0]['name']!r} but form a "
                "canonical+rendered group."
            ),
            items,
            severity="info",
        ))
    return findings


def all_render_linked(
    items: list[dict[str, Any]], links: dict[str, set[str]],
) -> bool:
    """True iff item IDs form one connected component under *links*.

    Shared with ``weld.agent_graph_audit._duplicate_names`` so the
    suppression predicate has exactly one definition.
    """
    ids = {item["id"] for item in items}
    if not ids:
        return False
    seen, stack = {next(iter(ids))}, [next(iter(ids))]
    while stack:
        for neighbor in links.get(stack.pop(), ()):
            if neighbor in ids and neighbor not in seen:
                seen.add(neighbor)
                stack.append(neighbor)
    return seen == ids


def suppressed_vague_findings(
    assets: list[dict[str, Any]],
    *,
    finding_factory: Callable[..., dict[str, Any]],
    norm: Callable[[str], str],
) -> list[dict[str, Any]]:
    """Info-level findings for vague-description checks silenced by ADR 0029.

    A rendered copy strips frontmatter (ADR 0026), so its description is
    blank by design. ``_vague_descriptions`` skips those derived/generated
    assets to avoid double-counting; strict mode reports them here so
    operators can audit the rendered surface explicitly.
    """
    findings: list[dict[str, Any]] = []
    for asset in assets:
        if asset["type"] not in _CLEAR_DESCRIPTION_TYPES:
            continue
        if asset["status"] not in {"derived", "generated"}:
            continue
        description = norm(asset["description"])
        words = [word for word in description.split() if word]
        if len(words) < 3 or description in _VAGUE_DESCRIPTIONS:
            findings.append(finding_factory(
                "vague_description_suppressed",
                "Suppressed vague description (rendered copy)",
                (
                    f"Suppressed by ADR 0029: {asset['type']} asset is "
                    "rendered/derived; the canonical source still gets "
                    "the description check."
                ),
                [asset],
                severity="info",
            ))
    return findings
