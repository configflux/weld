"""Shared "what is this" line for the node/match renderers in `_cli_render`.

Every human-readable match block and node header wants one short line
answering "what is this": ``props.description`` when an enrichment pass
(ADR 0079) has reviewed the node, ``props.summary`` -- a Python module's own
opening docstring paragraph (ADR 0114) -- when it has not. ``description``
wins when both are present: it is the reviewed field, ``summary`` is raw
discovery evidence carried under its own label rather than folded into the
same one, so a reader can tell which kind of claim they are looking at.
This module decides that precedence once, so :func:`weld._cli_render.
render_context` and the match-block helper shared by ``render_query``,
``render_callers`` and ``render_references`` cannot pick it differently.

Split out of :mod:`weld._cli_render`, which sat at the 400-line cap
(AGENTS.md / CLAUDE.md line-count policy) -- same reason and same pattern as
the existing :mod:`weld._cli_render_trust` split.

Both fields are repo-controlled text -- ``summary`` explicitly so (ADR
0114's consequences section, ADR 0115) -- and this module stays a pure
formatter of its input, same as the rest of ``weld._cli_render``: it returns
a string and touches no stream. Sanitization happens once, at the actual
write boundary in ``weld._graph_cli_emit._emit``, which wraps every
renderer's return value in ``weld._safe_text.sanitize_terminal_text`` before
it reaches a terminal -- so this module adds no new site for
``tools/lint_terminal_safety.py`` to guard.
"""

from __future__ import annotations

from typing import Any, Mapping


def prose_line(props: Mapping[str, Any], limit: int) -> str | None:
    """Return a rendered ``"label: text"`` line for *props*, or ``None``.

    ``description`` wins when both fields are present. Falls back to
    ``summary`` so a node with no enrichment pass still gets one line
    saying what it is -- the gap this module exists to close: discovery
    gives ~100% of Python file nodes a summary, but until now nothing
    rendered it. Returns ``None`` when neither field carries text, so
    callers can skip the line entirely rather than render an empty label.
    """
    desc = (props.get("description") or "").strip()
    if desc:
        return f"description: {_short(desc, limit)}"
    summary = (props.get("summary") or "").strip()
    if summary:
        return f"summary: {_short(summary, limit)}"
    return None


def _short(value: str, limit: int) -> str:
    flat = " ".join(value.split())
    if len(flat) <= limit:
        return flat
    return flat[: max(limit - 3, 0)] + "..."


__all__ = ["prose_line"]
