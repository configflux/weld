"""Small helpers for static Agent Graph metadata extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class AgentGraphReference:
    """One relationship extracted from static asset text or config."""

    target_type: str
    target_name: str
    edge_type: str
    line: int
    raw: str
    confidence: str = "definite"
    target_path: str | None = None


def ref(
    target_type: str,
    target_name: str,
    edge_type: str,
    line: int,
    raw: str,
    *,
    confidence: str = "definite",
    target_path: str | None = None,
) -> AgentGraphReference:
    """Create a normalized reference."""
    return AgentGraphReference(
        target_type=target_type,
        target_name=str(target_name).strip(),
        edge_type=edge_type,
        line=line,
        raw=str(raw),
        confidence=confidence,
        target_path=target_path,
    )


def dedupe_references(refs: Iterable[AgentGraphReference]) -> list[AgentGraphReference]:
    """Return references in first-seen order with exact duplicates removed."""
    result: list[AgentGraphReference] = []
    seen: set[tuple[object, ...]] = set()
    for item in refs:
        key = (
            item.target_type,
            item.target_name,
            item.edge_type,
            item.target_path,
        )
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def jsonable(value: Any) -> Any:
    """Return a deterministic JSON-compatible copy of a parsed config value."""
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, list):
        return [jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def string_list(value: Any) -> list[str]:
    """Normalize scalar or list config values into non-empty strings."""
    if value is None:
        return []
    if isinstance(value, list):
        values = value
    elif isinstance(value, tuple):
        values = list(value)
    elif isinstance(value, dict):
        values = value.keys()
    else:
        values = [value]
    result = [str(item).strip() for item in values if str(item).strip()]
    return sorted(dict.fromkeys(result))


def strings_for_keys(mapping: dict[str, Any], keys: Iterable[str]) -> list[str]:
    """Return normalized strings from the first matching keys in *mapping*."""
    result: list[str] = []
    for key in keys:
        if key in mapping:
            result.extend(string_list(mapping[key]))
    return sorted(dict.fromkeys(result))


def copy_first_scalar(
    target: dict[str, Any],
    source: dict[str, Any],
    prop_name: str,
    keys: Iterable[str],
) -> None:
    """Copy the first non-empty scalar under *keys* into *target*."""
    for key in keys:
        value = source.get(key)
        if isinstance(value, (str, int, float, bool)) and str(value).strip():
            target[prop_name] = str(value).strip()
            return


def copy_list(
    target: dict[str, Any],
    source: dict[str, Any],
    prop_name: str,
    keys: Iterable[str],
) -> None:
    """Copy normalized list metadata from *source* into *target*."""
    values = strings_for_keys(source, keys)
    if values:
        target[prop_name] = values


def clean_heading(value: str) -> str:
    """Normalize a Markdown heading into a compact label."""
    return re.sub(r"\s+", " ", value.strip().strip("#")).strip()


def first_paragraph(body: str) -> str | None:
    """Return the first prose paragraph after headings and list syntax."""
    lines: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("```"):
            if lines:
                break
            continue
        if line.startswith(("-", "*", "1.")):
            if lines:
                break
            continue
        lines.append(line)
    paragraph = " ".join(lines).strip()
    return paragraph or None


def named_entries(value: Any) -> list[tuple[str, Any]]:
    """Normalize object or list config entries into ``(name, value)`` pairs."""
    entries: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        entries.extend((str(name), config) for name, config in value.items())
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                entries.append((item["name"], item))
            else:
                entries.append((str(idx + 1), item))
    return sorted(entries, key=lambda item: item[0])


def iter_strings(value: Any) -> Iterable[str]:
    """Yield all string leaves from a parsed JSON-like value."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key in sorted(value, key=str):
            yield from iter_strings(value[key])
    elif isinstance(value, list):
        for item in value:
            yield from iter_strings(item)


def tool_name(value: str) -> str:
    """Reduce a permission expression like ``Bash(git status)`` to a tool."""
    return value.split("(", 1)[0].strip()


def is_external_ref(value: str) -> bool:
    """Return True for references that do not point at repository files."""
    lowered = value.lower()
    return (
        lowered.startswith(("http://", "https://", "mailto:", "data:"))
        or lowered.startswith("#")
    )


# --- Inferred-confidence body extraction (slice 1) --------------------------
#
# These regexes pick up orchestration references written in pseudocode style
# (subagent_type kwarg/colon, Skill() call, bare slash command) that the
# typed-prefix `_NAMED_REF_RE` cannot see. Edges emitted from these matches
# are tagged confidence="inferred" so callers can distinguish them from
# explicit `agent:`/`skill:`/`command:` references.
#
# The bare-slash pattern requires a known set of command names provided by
# the caller; without it any path like `/tmp/foo` would pollute the graph.

_SUBAGENT_TYPE_RE = re.compile(
    r"subagent_type\s*[:=]\s*[\"']([a-z][a-z0-9_-]*)[\"']"
)
_SKILL_CALL_RE = re.compile(
    r"Skill\s*\(\s*(?:skill_name|skill)\s*=\s*[\"']([a-z][a-z0-9_-]*)[\"']"
)
# Bare slash command: not preceded by a word character or a slash (so paths
# like /tmp/foo or //a/b are excluded), one lowercase word, terminated by
# whitespace, common punctuation, or end of line. The terminator class
# includes !, ?, ], } so prose like "Try /push!", "Should I /execute?",
# "[/plan](url)", and "`/cycle` for work}" all match (ukk8).
_BARE_COMMAND_RE = re.compile(
    r"(?<![A-Za-z0-9_/])/([a-z][a-z0-9-]*)(?=[\s.,;:)`*!?\]}]|$)"
)


def prose_inferred_references(
    mapping: dict[str, Any],
    keys: Iterable[str],
    *,
    line: int,
    known_commands: frozenset[str] | None,
) -> list[AgentGraphReference]:
    """Scan prose-bearing scalar values in *mapping* for inferred references.

    For each key in *keys*, if the value is a non-empty string, run the same
    body-text regexes (``subagent_type`` / ``Skill()`` / bare-``/command``)
    via :func:`extract_inferred_references`. Slice-3 (a1) k58t -- without
    this, references inside frontmatter ``description:`` (and aliases like
    ``desc`` / ``purpose``) are silently dropped.
    """
    refs: list[AgentGraphReference] = []
    for key in keys:
        raw = mapping.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        refs.extend(extract_inferred_references(
            raw, start_line=line, known_commands=known_commands,
        ))
    return refs


def extract_inferred_references(
    text: str,
    *,
    start_line: int,
    known_commands: frozenset[str] | None,
) -> list[AgentGraphReference]:
    """Return inferred-confidence references found in *text*.

    Scans line-by-line and emits:

    * `invokes_agent -> agent:<name>` for `subagent_type: "name"` /
      `subagent_type="name"` (literal lowercase identifiers only --
      template placeholders like `<implementer_type>` or `${var}` are
      filtered by the regex's leading `[a-z]` requirement).
    * `uses_skill -> skill:<name>` for `Skill(skill_name="name")` and
      `Skill(skill="name")`.
    * `uses_command -> command:<name>` for bare `/<name>` references --
      ONLY when *known_commands* is non-empty and contains *<name>*.
      Without that filter we would mint command nodes for every
      `/tmp/foo` and `/path/to/x` in the body.

    Every reference carries provenance: the file is the caller's
    responsibility (set on the edge by the materializer), and the line
    plus a copy of the matched text are attached here.
    """
    refs: list[AgentGraphReference] = []
    has_commands = bool(known_commands)
    for offset, line in enumerate(text.splitlines()):
        line_no = start_line + offset
        for match in _SUBAGENT_TYPE_RE.finditer(line):
            refs.append(ref(
                "agent",
                match.group(1),
                "invokes_agent",
                line_no,
                match.group(0),
                confidence="inferred",
            ))
        for match in _SKILL_CALL_RE.finditer(line):
            refs.append(ref(
                "skill",
                match.group(1),
                "uses_skill",
                line_no,
                match.group(0),
                confidence="inferred",
            ))
        if not has_commands:
            continue
        for match in _BARE_COMMAND_RE.finditer(line):
            name = match.group(1)
            if name not in known_commands:
                continue
            refs.append(ref(
                "command",
                name,
                "uses_command",
                line_no,
                match.group(0),
                confidence="inferred",
            ))
    return refs


def diagnostic(
    code: str, path: str, message: str, *,
    line: int | None = None, reference: str | None = None, raw: str | None = None,
) -> dict[str, Any]:
    """Build a parser diagnostic dict with optional line/reference/raw fields."""
    result: dict[str, Any] = {"severity": "warning", "code": code, "path": path, "message": message}
    if line is not None:
        result["line"] = line
    if reference is not None:
        result["reference"] = reference
    if raw is not None:
        result["raw"] = raw
    return result
