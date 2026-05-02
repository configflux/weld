"""Per-entry permission allow/deny edge extraction.

Previously, distinct ``Bash(...)`` allow entries in ``.claude/settings.json``
collapsed to a single ``provides_tool`` edge because :func:`tool_name`
strips the parameter list and the metadata-level dedupe key was target-
name-only. This module emits one reference per allow/deny entry with the
full pattern in ``raw`` and the list-entry's line number, so the
materializer's per-edge dedupe (keyed on raw) keeps each as a distinct
edge. Edges still aggregate at the ``tool:<name>`` node, preserving
audit semantics (permission_conflict, etc).
"""

from __future__ import annotations

from typing import Any

from weld.agent_graph_metadata_utils import (
    AgentGraphReference, ref, tool_name,
)


def permission_references_with_lines(
    payload: dict[str, Any], text: str,
) -> list[AgentGraphReference]:
    """Emit one ref per permission allow/deny entry with per-entry line."""
    refs: list[AgentGraphReference] = []
    permissions = payload.get("permissions")
    if not isinstance(permissions, dict):
        return refs
    line_index = _build_string_line_index(text)
    for kind, edge in (("allow", "provides_tool"), ("deny", "restricts_tool")):
        items = permissions.get(kind)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, str) or not item.strip():
                continue
            entry_line = _consume_string_line(line_index, item)
            refs.append(ref(
                "tool", tool_name(item), edge, entry_line, item,
            ))
    return refs


def _build_string_line_index(text: str) -> dict[str, list[int]]:
    """Map each JSON string literal to its 1-based line numbers in *text*.

    The decoder walks the text byte-by-byte, tracking the opening line of
    each double-quoted string and decoding standard JSON escapes. Multiple
    occurrences of the same string are recorded in document order so each
    consumer call hands back the next entry's line.
    """
    index: dict[str, list[int]] = {}
    line = 1
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\n":
            line += 1
            i += 1
            continue
        if ch != "\"":
            i += 1
            continue
        start_line = line
        i += 1
        buf: list[str] = []
        while i < n:
            c = text[i]
            if c == "\\" and i + 1 < n:
                esc = text[i + 1]
                buf.append(_JSON_ESCAPES.get(esc, esc))
                if esc == "\n":
                    line += 1
                i += 2
                continue
            if c == "\"":
                i += 1
                break
            if c == "\n":
                line += 1
            buf.append(c)
            i += 1
        index.setdefault("".join(buf), []).append(start_line)
    return index


_JSON_ESCAPES = {
    "\"": "\"", "\\": "\\", "/": "/",
    "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t",
}


def _consume_string_line(index: dict[str, list[int]], item: str) -> int:
    """Pop and return the next recorded line number for *item* (or 1)."""
    occurrences = index.get(item)
    if occurrences:
        return occurrences.pop(0)
    return 1
