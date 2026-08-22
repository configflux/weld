"""``wd stale`` diverging-source rendering.

Split out of :mod:`weld._cli_render` so that module stays under its
line-count cap, mirroring the existing :mod:`weld._cli_render_trust` /
:mod:`weld._cli_render_prose` pattern: return raw ``list[str]`` line
fragments that :func:`weld._cli_render.render_stale` merges into its own
``lines`` before the single ``_header``/``_join`` pass, rather than a
second rendered block competing with the first.
"""

from __future__ import annotations

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
