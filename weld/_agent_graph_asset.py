"""Parsed-asset types and the shared extraction helpers that both static
Agent Graph metadata parsers need -- the dependency-free leaf under
``weld.agent_graph_metadata`` / ``weld.agent_graph_metadata_toml``.

Split out of :mod:`weld.agent_graph_metadata` (bd 5038-ujv26, ADR 0130
disposition #9): ``agent_graph_metadata_toml.parse_toml_asset()`` needed
``ParsedAgentGraphAsset`` plus four private helpers (``_config_props``,
``_mcp_nodes``, ``_metadata_references``, ``_text_references``) from
``agent_graph_metadata.py`` and could only reach them with a function-local
import, while ``agent_graph_metadata.py`` needed ``parse_toml_asset`` back
from the TOML module for its own platform-config dispatch -- a real
2-member file cycle (both edges are genuine load-bearing symbol needs, not
an accident of layout).

``DerivedAgentGraphNode`` moves too, though it is not one of the four named
helpers: ``_mcp_nodes`` constructs it directly and
``ParsedAgentGraphAsset.derived_nodes`` is typed on it, so leaving it behind
would force this leaf to import back from ``agent_graph_metadata.py`` --
recreating the exact cycle this split removes. ``_append_file_ref`` moves
with ``_text_references`` for the same reason: it is a private helper used
only by that function.

This module holds no import of :mod:`weld.agent_graph_metadata` or
:mod:`weld.agent_graph_metadata_toml`, so nothing importing it can cycle
back. ``agent_graph_metadata.py`` imports from here and re-exports
``ParsedAgentGraphAsset`` / ``DerivedAgentGraphNode`` for its existing
public surface (``weld.agent_graph_materialize`` imports both by name); the
four private helpers keep their underscore-prefixed names here, per this
repo's sibling-split convention (private-per-name, not private-per-file).
``agent_graph_metadata_toml.py`` imports everything it needs from here
directly instead of reaching back into ``agent_graph_metadata.py`` -- the
edge that broke the cycle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from weld.agent_graph_metadata_utils import (
    AgentGraphReference,
    copy_first_scalar,
    copy_list,
    dedupe_references,
    extract_inferred_references,
    is_external_ref,
    named_entries,
    prose_inferred_references,
    ref,
    strings_for_keys,
)

_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_AT_FILE_RE = re.compile(r"(?<![\w/])@([A-Za-z0-9_./-]+\.[A-Za-z0-9]+)")
# The trailing (?!\w) anchors the extension at a word boundary; without it,
# greedy backtracking lets a shorter known extension match as a prefix of a
# longer one (.tsv reported as .ts, .jsonl as .json) and the truncated path
# is then flagged as a broken reference.
_PATH_RE = re.compile(
    r"(?<![\w@./-])((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+"
    r"\.(?:md|mdc|json|jsonl|ya?ml|toml|txt|tsv|py|js|jsx|ts|tsx|sh|bash))"
    r"(?!\w)"
)
_NAMED_REF_RE = re.compile(
    r"\b(skill|agent|command|mcp|mcp-server):([A-Za-z0-9_.-]+)\b"
)
_GLOB_CHARS = set("*?[")

# Shared with weld.agent_graph_metadata._frontmatter_props (frontmatter
# props read the same closed vocabulary that reference extraction below
# scans) -- defined once here, imported back by the metadata module, so the
# two never drift.
_DESCRIPTION_KEYS = ("description", "desc", "purpose")
_TOOL_KEYS = ("tools", "allowed_tools", "allowedTools")
_DENIED_TOOL_KEYS = ("denied_tools", "deniedTools", "forbidden_tools")
_HANDOFF_KEYS = ("handoffs", "handoff_to", "handoffTo", "delegates_to")
_PATH_KEYS = ("applyTo", "applies_to", "paths", "path_globs", "globs")
# Consumed only by _metadata_references below -- no sibling in
# agent_graph_metadata.py reads these.
_SKILL_KEYS = ("skills", "uses_skills", "usesSkills")
_COMMAND_KEYS = ("commands", "uses_commands", "usesCommands")
_MCP_KEYS = ("mcp", "mcp_servers", "mcpServers")
# Authoritative orchestrator-pipeline declarations live under the ``weld:``
# namespace in frontmatter (see ADR 0021 Amendment 1). Edges from these keys
# emit ``invokes_agent`` at confidence=definite, dedupe-winning over any
# inferred-confidence edge produced by body regex on the same target.
_INVOKES_AGENT_KEYS = ("invokes_agents", "subagents", "dispatches_to")


@dataclass(frozen=True)
class DerivedAgentGraphNode:
    """A node declared inside a larger static config file."""

    node_type: str
    name: str
    platform: str
    path: str
    source_kind: str
    props: dict[str, Any] = field(default_factory=dict)
    references: tuple[AgentGraphReference, ...] = ()


@dataclass(frozen=True)
class ParsedAgentGraphAsset:
    """Metadata extracted from one discovered customization asset."""

    props: dict[str, Any] = field(default_factory=dict)
    references: tuple[AgentGraphReference, ...] = ()
    derived_nodes: tuple[DerivedAgentGraphNode, ...] = ()
    diagnostics: tuple[dict[str, Any], ...] = ()


def _config_props(payload: dict[str, Any]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    copy_first_scalar(props, payload, "description", _DESCRIPTION_KEYS)
    copy_list(props, payload, "tools", _TOOL_KEYS)
    copy_list(props, payload, "denied_tools", _DENIED_TOOL_KEYS)
    return props


def _mcp_nodes(rel_path: str, platform: str, payload: dict[str, Any]) -> list[DerivedAgentGraphNode]:
    entries = payload.get("mcpServers") or payload.get("mcp_servers") or payload.get("mcp")
    nodes: list[DerivedAgentGraphNode] = []
    for name, config in named_entries(entries):
        props = _config_props(config) if isinstance(config, dict) else {}
        nodes.append(DerivedAgentGraphNode(
            node_type="mcp-server", name=name, platform=platform,
            path=f"{rel_path}#/mcpServers/{name}", source_kind=f"{platform}-mcp-server", props=props,
        ))
    return nodes


def _metadata_references(value: Any, *, line: int, known_commands: frozenset[str] | None = None) -> list[AgentGraphReference]:
    refs: list[AgentGraphReference] = []
    if not isinstance(value, dict):
        return refs
    for item in strings_for_keys(value, _TOOL_KEYS):
        refs.append(ref("tool", item, "provides_tool", line, item))
    for item in strings_for_keys(value, _DENIED_TOOL_KEYS):
        refs.append(ref("tool", item, "restricts_tool", line, item))
    for item in strings_for_keys(value, _HANDOFF_KEYS):
        refs.append(ref("agent", item, "handoff_to", line, item))
    for item in strings_for_keys(value, _PATH_KEYS):
        refs.append(ref("scope", item, "applies_to_path", line, item))
    for item in strings_for_keys(value, _SKILL_KEYS):
        refs.append(ref("skill", item, "uses_skill", line, item))
    for item in strings_for_keys(value, _COMMAND_KEYS):
        refs.append(ref("command", item, "uses_command", line, item))
    for item in strings_for_keys(value, _MCP_KEYS):
        refs.append(ref("mcp-server", item, "provides_tool", line, item))
    # Slice 2: orchestrator pipeline declarations live under ``weld:`` in
    # frontmatter (canonical), but accept top-level for forward compat.
    weld_ns = value.get("weld") if isinstance(value.get("weld"), dict) else {}
    for item in strings_for_keys(value, _INVOKES_AGENT_KEYS):
        refs.append(ref("agent", item, "invokes_agent", line, item))
    for item in strings_for_keys(weld_ns, _INVOKES_AGENT_KEYS):
        refs.append(ref("agent", item, "invokes_agent", line, item))
    # Permission allow/deny entries are exploded per-entry by the JSON parser
    # (permission_references_with_lines); not handled here.
    # Slice-3 (a1) k58t: scan prose-bearing scalars (description/desc/purpose)
    # via the same body-text inferred-edge regex. Same filter & contract.
    refs.extend(prose_inferred_references(value, _DESCRIPTION_KEYS, line=line, known_commands=known_commands))
    return refs


def _text_references(
    text: str,
    *,
    start_line: int,
    known_commands: frozenset[str] | None = None,
) -> list[AgentGraphReference]:
    refs: list[AgentGraphReference] = []
    for offset, line in enumerate(text.splitlines()):
        line_no = start_line + offset
        for match in _MARKDOWN_LINK_RE.finditer(line):
            _append_file_ref(refs, match.group(1), line_no)
        for match in _AT_FILE_RE.finditer(line):
            _append_file_ref(refs, match.group(1), line_no)
        for match in _PATH_RE.finditer(line):
            _append_file_ref(refs, match.group(1), line_no)
        for match in _NAMED_REF_RE.finditer(line):
            kind, name = match.groups()
            target_type = "mcp-server" if kind in {"mcp", "mcp-server"} else kind
            edge_type = {
                "agent": "invokes_agent",
                "command": "uses_command",
                "mcp-server": "provides_tool",
                "skill": "uses_skill",
            }[target_type]
            refs.append(ref(target_type, name, edge_type, line_no, match.group(0)))
    refs.extend(extract_inferred_references(
        text, start_line=start_line, known_commands=known_commands,
    ))
    return dedupe_references(refs)


def _append_file_ref(refs: list[AgentGraphReference], raw: str, line: int) -> None:
    target = raw.split("#", 1)[0].strip()
    if not target or is_external_ref(target) or any(ch in target for ch in _GLOB_CHARS):
        return
    refs.append(ref("file", target, "references_file", line, raw, target_path=target))
