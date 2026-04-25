"""Static metadata and reference extraction for Agent Graph assets."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from weld._yaml import parse_yaml
from weld.agent_graph_authority import (
    frontmatter_authority_props,
    generated_marker_props,
)
from weld.agent_graph_metadata_utils import (
    AgentGraphReference,
    clean_heading,
    copy_first_scalar,
    copy_list,
    dedupe_references,
    first_paragraph,
    is_external_ref,
    iter_strings,
    jsonable,
    named_entries,
    ref,
    string_list,
    strings_for_keys,
    tool_name,
)

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_AT_FILE_RE = re.compile(r"(?<![\w/])@([A-Za-z0-9_./-]+\.[A-Za-z0-9]+)")
_PATH_RE = re.compile(
    r"(?<![\w@./-])((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+"
    r"\.(?:md|mdc|json|ya?ml|toml|txt|py|js|jsx|ts|tsx|sh|bash))"
)
_NAMED_REF_RE = re.compile(
    r"\b(skill|agent|command|mcp|mcp-server):([A-Za-z0-9_.-]+)\b"
)
_HEADING_RE = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)
_GLOB_CHARS = set("*?[")

_DESCRIPTION_KEYS = ("description", "desc", "purpose")
_TOOL_KEYS = ("tools", "allowed_tools", "allowedTools")
_DENIED_TOOL_KEYS = ("denied_tools", "deniedTools", "forbidden_tools")
_HANDOFF_KEYS = ("handoffs", "handoff_to", "handoffTo", "delegates_to")
_PATH_KEYS = ("applyTo", "applies_to", "paths", "path_globs", "globs")
_SKILL_KEYS = ("skills", "uses_skills", "usesSkills")
_COMMAND_KEYS = ("commands", "uses_commands", "usesCommands")
_MCP_KEYS = ("mcp", "mcp_servers", "mcpServers")


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


def parse_agent_asset(root: Path, rel_path: str, node_type: str, platform: str) -> ParsedAgentGraphAsset:
    """Parse static metadata and references from one discovered asset."""
    path = root / rel_path
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return ParsedAgentGraphAsset(diagnostics=(_diagnostic(
            "agent_graph_unreadable_asset",
            rel_path,
            f"Could not read AI customization asset as UTF-8: {exc}",
        ),))

    if rel_path.endswith(".json"):
        return _parse_json_asset(root, rel_path, platform, text)
    return _parse_markdown_asset(root, rel_path, node_type, text)


def _parse_markdown_asset(
    root: Path,
    rel_path: str,
    node_type: str,
    text: str,
) -> ParsedAgentGraphAsset:
    frontmatter, body, body_line = _split_frontmatter(text)
    props = _frontmatter_props(frontmatter)
    props.update(generated_marker_props(text))
    if node_type == "skill":
        props.update({k: v for k, v in _skill_props(body).items() if k not in props})

    references = list(_metadata_references(frontmatter, line=1))
    references.extend(_text_references(body, start_line=body_line))
    references = dedupe_references(references)
    diagnostics = _broken_file_diagnostics(root, rel_path, references)
    return ParsedAgentGraphAsset(
        props=props,
        references=tuple(dedupe_references(references)),
        diagnostics=tuple(diagnostics),
    )


def _parse_json_asset(
    root: Path,
    rel_path: str,
    platform: str,
    text: str,
) -> ParsedAgentGraphAsset:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return ParsedAgentGraphAsset(diagnostics=(_diagnostic(
            "agent_graph_invalid_json",
            rel_path,
            f"Could not parse JSON customization config: {exc.msg}",
            line=exc.lineno,
        ),))
    if not isinstance(payload, dict):
        return ParsedAgentGraphAsset()

    props = _config_props(payload)
    derived = _derived_json_nodes(rel_path, platform, payload)
    refs = list(_metadata_references(payload, line=1))
    for raw in iter_strings(payload):
        refs.extend(_text_references(raw, start_line=1))
    refs = dedupe_references(refs)
    diagnostics = _broken_file_diagnostics(root, rel_path, refs)
    for node in derived:
        diagnostics.extend(_broken_file_diagnostics(root, rel_path, node.references))
    return ParsedAgentGraphAsset(
        props=props,
        references=tuple(dedupe_references(refs)),
        derived_nodes=tuple(derived),
        diagnostics=tuple(diagnostics),
    )


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str, int]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text, 1
    parsed = parse_yaml(match.group(1))
    frontmatter = parsed if isinstance(parsed, dict) else {}
    body_start_line = text[: match.end()].count("\n") + 1
    return frontmatter, text[match.end():], body_start_line


def _frontmatter_props(frontmatter: dict[str, Any]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    if not frontmatter:
        return props
    props["frontmatter"] = jsonable(frontmatter)
    copy_first_scalar(props, frontmatter, "name", ("name",))
    copy_first_scalar(props, frontmatter, "description", _DESCRIPTION_KEYS)
    copy_first_scalar(props, frontmatter, "model", ("model", "model_hint", "modelHint"))
    copy_list(props, frontmatter, "tools", _TOOL_KEYS)
    copy_list(props, frontmatter, "denied_tools", _DENIED_TOOL_KEYS)
    copy_list(props, frontmatter, "handoffs", _HANDOFF_KEYS)
    copy_list(props, frontmatter, "path_globs", _PATH_KEYS)
    props.update(frontmatter_authority_props(frontmatter))
    return props


def _skill_props(body: str) -> dict[str, Any]:
    props: dict[str, Any] = {}
    heading = _HEADING_RE.search(body)
    if heading:
        props["name"] = clean_heading(heading.group(1))
    description = first_paragraph(body)
    if description:
        props["description"] = description
    return props


def _config_props(payload: dict[str, Any]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    copy_first_scalar(props, payload, "description", _DESCRIPTION_KEYS)
    copy_list(props, payload, "tools", _TOOL_KEYS)
    copy_list(props, payload, "denied_tools", _DENIED_TOOL_KEYS)
    return props


def _derived_json_nodes(
    rel_path: str,
    platform: str,
    payload: dict[str, Any],
) -> list[DerivedAgentGraphNode]:
    nodes: list[DerivedAgentGraphNode] = []
    if platform == "opencode":
        nodes.extend(_configured_nodes(rel_path, platform, payload, "agents", "agent"))
        nodes.extend(_configured_nodes(rel_path, platform, payload, "commands", "command"))
    nodes.extend(_mcp_nodes(rel_path, platform, payload))
    nodes.extend(_hook_nodes(rel_path, platform, payload))
    return nodes


def _configured_nodes(
    rel_path: str,
    platform: str,
    payload: dict[str, Any],
    key: str,
    node_type: str,
) -> list[DerivedAgentGraphNode]:
    entries = payload.get(key) or payload.get(key[:-1])
    nodes: list[DerivedAgentGraphNode] = []
    for name, config in named_entries(entries):
        props = _config_props(config) if isinstance(config, dict) else {}
        refs = list(_metadata_references(config, line=1)) if isinstance(config, dict) else []
        if isinstance(config, dict):
            copy_first_scalar(props, config, "model", ("model",))
        for raw in iter_strings(config):
            refs.extend(_text_references(raw, start_line=1))
        nodes.append(DerivedAgentGraphNode(
            node_type=node_type,
            name=name,
            platform=platform,
            path=f"{rel_path}#/{key}/{name}",
            source_kind=f"{platform}-{node_type}",
            props=props,
            references=tuple(dedupe_references(refs)),
        ))
    return nodes


def _mcp_nodes(rel_path: str, platform: str, payload: dict[str, Any]) -> list[DerivedAgentGraphNode]:
    entries = payload.get("mcpServers") or payload.get("mcp_servers") or payload.get("mcp")
    nodes: list[DerivedAgentGraphNode] = []
    for name, config in named_entries(entries):
        props = _config_props(config) if isinstance(config, dict) else {}
        nodes.append(DerivedAgentGraphNode(
            node_type="mcp-server",
            name=name,
            platform=platform,
            path=f"{rel_path}#/mcpServers/{name}",
            source_kind=f"{platform}-mcp-server",
            props=props,
        ))
    return nodes


def _hook_nodes(rel_path: str, platform: str, payload: dict[str, Any]) -> list[DerivedAgentGraphNode]:
    hooks = payload.get("hooks")
    if not isinstance(hooks, dict):
        return []
    nodes: list[DerivedAgentGraphNode] = []
    for event in sorted(hooks):
        entries = hooks[event] if isinstance(hooks[event], list) else [hooks[event]]
        for idx, entry in enumerate(entries):
            name = f"{event}-{idx + 1}"
            props = {"event": event}
            if isinstance(entry, dict):
                props.update(_config_props(entry))
                matcher = entry.get("matcher")
                if isinstance(matcher, str) and matcher:
                    props["matcher"] = matcher
            refs = []
            for raw in iter_strings(entry):
                refs.extend(_text_references(raw, start_line=1))
            nodes.append(DerivedAgentGraphNode(
                node_type="hook",
                name=name,
                platform=platform,
                path=f"{rel_path}#/hooks/{event}/{idx}",
                source_kind=f"{platform}-hook",
                props=props,
                references=tuple(dedupe_references(refs)),
            ))
    return nodes


def _metadata_references(value: Any, *, line: int) -> list[AgentGraphReference]:
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
    permissions = value.get("permissions")
    if isinstance(permissions, dict):
        for item in string_list(permissions.get("allow")):
            refs.append(ref("tool", tool_name(item), "provides_tool", line, item))
        for item in string_list(permissions.get("deny")):
            refs.append(ref("tool", tool_name(item), "restricts_tool", line, item))
    return refs


def _text_references(text: str, *, start_line: int) -> list[AgentGraphReference]:
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
    return dedupe_references(refs)


def _append_file_ref(refs: list[AgentGraphReference], raw: str, line: int) -> None:
    target = raw.split("#", 1)[0].strip()
    if not target or is_external_ref(target) or any(ch in target for ch in _GLOB_CHARS):
        return
    refs.append(ref("file", target, "references_file", line, raw, target_path=target))


def _broken_file_diagnostics(
    root: Path,
    source_rel: str,
    refs: list[AgentGraphReference] | tuple[AgentGraphReference, ...],
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for reference in refs:
        if reference.target_type != "file":
            continue
        if _resolve_file(root, source_rel, reference.target_path or reference.target_name) is None:
            diagnostics.append(_diagnostic(
                "agent_graph_broken_reference",
                source_rel,
                f"Referenced file does not exist: {reference.target_name}",
                line=reference.line,
                reference=reference.target_name,
                raw=reference.raw,
            ))
    return diagnostics


def _resolve_file(root: Path, source_rel: str, target: str) -> str | None:
    candidates = []
    if not target.startswith("/"):
        candidates.append(root / target)
        candidates.append((root / source_rel).parent / target)
    for candidate in candidates:
        try:
            rel = candidate.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            continue
        if (root / rel).is_file():
            return rel
    return None


def _diagnostic(
    code: str,
    path: str,
    message: str,
    *,
    line: int | None = None,
    reference: str | None = None,
    raw: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"severity": "warning", "code": code, "path": path, "message": message}
    if line is not None:
        result["line"] = line
    if reference is not None:
        result["reference"] = reference
    if raw is not None:
        result["raw"] = raw
    return result
