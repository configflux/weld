"""Static metadata and reference extraction for Agent Graph assets."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from weld._agent_graph_asset import (
    DerivedAgentGraphNode,
    ParsedAgentGraphAsset,
    _DENIED_TOOL_KEYS,
    _DESCRIPTION_KEYS,
    _HANDOFF_KEYS,
    _PATH_KEYS,
    _TOOL_KEYS,
    _config_props,
    _mcp_nodes,
    _metadata_references,
    _text_references,
)
from weld._yaml import parse_yaml
from weld.agent_graph_authority import (
    frontmatter_authority_props,
    generated_marker_props,
)
from weld.agent_graph_metadata_diagnostics import broken_file_diagnostics
from weld.agent_graph_metadata_permissions import permission_references_with_lines
from weld.agent_graph_metadata_utils import (
    AgentGraphReference,  # noqa: F401 -- re-exported for callers (weld.agent_graph_materialize)
    clean_heading,
    copy_first_scalar,
    copy_list,
    dedupe_references,
    diagnostic as _diagnostic,
    first_paragraph,
    iter_strings,
    jsonable,
    named_entries,
    ref,
)

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
_HEADING_RE = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)


def parse_agent_asset(
    root: Path, rel_path: str, node_type: str, platform: str,
    *, known_commands: frozenset[str] | None = None,
) -> ParsedAgentGraphAsset:
    """Parse static metadata and references from one discovered asset.

    *known_commands* gates body-text bare-slash command extraction so that
    paths like ``/tmp/foo`` are not minted as command edges; pass ``None``
    when no command set has been discovered yet (subagent_type and Skill()
    extraction still works because they cannot be confused with file paths).
    """
    path = root / rel_path
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return ParsedAgentGraphAsset(diagnostics=(_diagnostic(
            "agent_graph_unreadable_asset", rel_path,
            f"Could not read AI customization asset as UTF-8: {exc}",
        ),))

    if rel_path.endswith(".json"):
        return _parse_json_asset(root, rel_path, platform, text, known_commands)
    if rel_path.endswith(".toml"):
        from weld.agent_graph_metadata_toml import parse_toml_asset
        return parse_toml_asset(root, rel_path, platform, text, known_commands)
    return _parse_markdown_asset(root, rel_path, node_type, text, known_commands)


def _parse_markdown_asset(
    root: Path, rel_path: str, node_type: str, text: str, known_commands: frozenset[str] | None,
) -> ParsedAgentGraphAsset:
    frontmatter, body, body_line = _split_frontmatter(text)
    props = _frontmatter_props(frontmatter)
    props.update(generated_marker_props(text))
    if node_type == "skill":
        props.update({k: v for k, v in _skill_props(body).items() if k not in props})

    references = list(_metadata_references(frontmatter, line=1, known_commands=known_commands))
    references.extend(_text_references(body, start_line=body_line, known_commands=known_commands))
    # ADR 0021 Amendment 2 (5i8b): instruction files default to repo-wide scope.
    if node_type == "instruction" and not any(r.edge_type == "applies_to_path" for r in references):
        references.append(ref("scope", "**", "applies_to_path", 1, "**", confidence="inferred"))
    references = dedupe_references(references)
    diagnostics = broken_file_diagnostics(root, rel_path, references)
    return ParsedAgentGraphAsset(
        props=props,
        references=tuple(dedupe_references(references)),
        diagnostics=tuple(diagnostics),
    )


def _parse_json_asset(
    root: Path, rel_path: str, platform: str, text: str, known_commands: frozenset[str] | None,
) -> ParsedAgentGraphAsset:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return ParsedAgentGraphAsset(diagnostics=(_diagnostic(
            "agent_graph_invalid_json", rel_path,
            f"Could not parse JSON customization config: {exc.msg}", line=exc.lineno,
        ),))
    if not isinstance(payload, dict):
        return ParsedAgentGraphAsset()

    props = _config_props(payload)
    derived = _derived_json_nodes(rel_path, platform, payload, known_commands)
    refs = list(_metadata_references(payload, line=1, known_commands=known_commands))
    for raw in iter_strings(payload):
        refs.extend(_text_references(raw, start_line=1, known_commands=known_commands))
    # Permission allow/deny entries are exploded per-entry and appended
    # AFTER dedupe so multiple Bash(...) patterns survive; the
    # materializer's per-edge dedupe is keyed on raw.
    final_refs = dedupe_references(refs) + permission_references_with_lines(payload, text)
    diagnostics = broken_file_diagnostics(root, rel_path, final_refs)
    for node in derived:
        diagnostics.extend(broken_file_diagnostics(root, rel_path, node.references))
    return ParsedAgentGraphAsset(
        props=props, references=tuple(final_refs),
        derived_nodes=tuple(derived), diagnostics=tuple(diagnostics),
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


def _derived_json_nodes(
    rel_path: str, platform: str, payload: dict[str, Any], known_commands: frozenset[str] | None,
) -> list[DerivedAgentGraphNode]:
    nodes: list[DerivedAgentGraphNode] = []
    if platform == "opencode":
        nodes.extend(_configured_nodes(rel_path, platform, payload, "agents", "agent", known_commands))
        nodes.extend(_configured_nodes(rel_path, platform, payload, "commands", "command", known_commands))
    nodes.extend(_mcp_nodes(rel_path, platform, payload))
    nodes.extend(_hook_nodes(rel_path, platform, payload, known_commands))
    return nodes


def _configured_nodes(
    rel_path: str, platform: str, payload: dict[str, Any], key: str, node_type: str,
    known_commands: frozenset[str] | None,
) -> list[DerivedAgentGraphNode]:
    entries = payload.get(key) or payload.get(key[:-1])
    nodes: list[DerivedAgentGraphNode] = []
    for name, config in named_entries(entries):
        props = _config_props(config) if isinstance(config, dict) else {}
        refs = list(_metadata_references(config, line=1, known_commands=known_commands)) if isinstance(config, dict) else []
        if isinstance(config, dict):
            copy_first_scalar(props, config, "model", ("model",))
        for raw in iter_strings(config):
            refs.extend(_text_references(raw, start_line=1, known_commands=known_commands))
        nodes.append(DerivedAgentGraphNode(
            node_type=node_type, name=name, platform=platform,
            path=f"{rel_path}#/{key}/{name}", source_kind=f"{platform}-{node_type}",
            props=props, references=tuple(dedupe_references(refs)),
        ))
    return nodes


def _hook_nodes(
    rel_path: str, platform: str, payload: dict[str, Any], known_commands: frozenset[str] | None,
) -> list[DerivedAgentGraphNode]:
    hooks = payload.get("hooks")
    if not isinstance(hooks, dict):
        return []
    nodes: list[DerivedAgentGraphNode] = []
    for event in sorted(hooks):
        entries = hooks[event] if isinstance(hooks[event], list) else [hooks[event]]
        for idx, entry in enumerate(entries):
            name = f"{event}-{idx + 1}"
            props: dict[str, Any] = {"event": event}
            if isinstance(entry, dict):
                props.update(_config_props(entry))
                matcher = entry.get("matcher")
                if isinstance(matcher, str) and matcher:
                    props["matcher"] = matcher
            refs = []
            for raw in iter_strings(entry):
                refs.extend(_text_references(raw, start_line=1, known_commands=known_commands))
            nodes.append(DerivedAgentGraphNode(
                node_type="hook", name=name, platform=platform,
                path=f"{rel_path}#/hooks/{event}/{idx}", source_kind=f"{platform}-hook",
                props=props, references=tuple(dedupe_references(refs)),
            ))
    return nodes
