"""Static discovery core for AI customization assets.

This scanner is intentionally static: it only inspects repo-visible files and
never executes project-local scripts, hooks, commands, LLM calls, or network
calls.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from weld.agent_graph_authority import (
    apply_authority_config,
    authority_sources,
    load_authority_config,
)
from weld.agent_graph_materialize import (
    diagnostics_for_assets,
    materialize_agent_graph,
)
from weld.agent_graph_metadata import parse_agent_asset
from weld.agent_graph_storage import build_agent_graph
from weld.repo_boundary import iter_repo_files

DISCOVERY_SOURCE = "agent_graph_static"

_PLATFORM_LABELS: dict[str, str] = {
    "claude": "Claude Code",
    "codex": "Codex",
    "cursor": "Cursor",
    "gemini": "Gemini CLI",
    "generic": "Generic",
    "github-copilot": "GitHub Copilot / VS Code",
    "opencode": "OpenCode",
}

_EXACT_RULES: dict[str, tuple[str, str, str]] = {
    ".github/copilot-instructions.md": (
        "instruction",
        "github-copilot",
        "copilot-instructions",
    ),
    ".claude/settings.json": ("config", "claude", "settings"),
    ".codex/config.toml": ("config", "codex", "codex-config"),
    ".mcp.json": ("config", "generic", "mcp"),
    "AGENTS.md": ("instruction", "generic", "agents"),
    "AGENTS.override.md": ("instruction", "codex", "agents-override"),
    "CLAUDE.md": ("instruction", "claude", "claude"),
    "GEMINI.md": ("instruction", "gemini", "gemini"),
    "opencode.json": ("config", "opencode", "opencode"),
}

_NAME_SUFFIXES = (
    ".instructions.md",
    ".agent.md",
    ".prompt.md",
    ".mdc",
    ".md",
    ".json",
)

@dataclass(frozen=True)
class AgentCustomizationAsset:
    """One statically discovered AI customization file."""

    path: str
    node_type: str
    name: str
    platform: str
    platform_name: str
    source_kind: str

    def props(self) -> dict[str, Any]:
        return {
            "file": self.path,
            "name": self.name,
            "platform": self.platform,
            "platform_name": self.platform_name,
            "source_kind": self.source_kind,
            "source_platform": self.platform,
            "source_strategy": DISCOVERY_SOURCE,
            "status": "manual",
        }


def discover_agent_assets(root: Path) -> list[AgentCustomizationAsset]:
    """Return all statically discoverable Agent Graph assets under *root*."""
    assets = [
        asset
        for path in iter_repo_files(root)
        if (asset := _classify_file(_repo_relative(root, path))) is not None
    ]
    assets.sort(key=lambda asset: asset.path)
    return assets


def discover_agent_graph(
    root: Path,
    *,
    diagnostics: list[dict] | None = None,
    git_sha: str | None = None,
    updated_at: str | None = None,
) -> dict[str, Any]:
    """Build the static Agent Graph for *root* without writing it.

    Discovery runs in two passes so body-text bare-slash command extraction
    can be filtered against the actual command set: pass 1 classifies all
    assets and probes them (with no known-command set) to collect both
    file-classified ``command`` assets and JSON-derived command nodes; pass
    2 re-parses each asset with that frozenset wired through to the
    inferred-edge regexes. Without this filter every ``/tmp/foo`` in body
    text would mint a spurious command edge.
    """
    assets = discover_agent_assets(root)
    authority_config = load_authority_config(root)
    probe = {
        asset.path: parse_agent_asset(
            root, asset.path, asset.node_type, asset.platform, known_commands=None,
        )
        for asset in assets
    }
    discovered_commands: set[str] = {
        asset.name for asset in assets if asset.node_type == "command"
    }
    for parsed_asset in probe.values():
        for derived in parsed_asset.derived_nodes:
            if derived.node_type == "command":
                discovered_commands.add(derived.name)
    known_commands = frozenset(discovered_commands)
    parsed = {
        asset.path: parse_agent_asset(
            root, asset.path, asset.node_type, asset.platform,
            known_commands=known_commands,
        )
        for asset in assets
    }
    nodes, edges, source_ids = materialize_agent_graph(
        root,
        assets,
        parsed,
        platform_labels=_PLATFORM_LABELS,
        source_strategy=DISCOVERY_SOURCE,
    )
    authority_diagnostics = apply_authority_config(
        nodes, edges, authority_config, source_strategy=DISCOVERY_SOURCE,
    )
    parsed_diagnostics = diagnostics_for_assets(assets, parsed, source_ids)
    return build_agent_graph(
        root=root,
        nodes=nodes,
        edges=edges,
        discovered_from=[asset.path for asset in assets] + authority_sources(authority_config),
        diagnostics=(
            copy.deepcopy(diagnostics or [])
            + parsed_diagnostics
            + authority_diagnostics
        ),
        git_sha=git_sha,
        updated_at=updated_at,
    )


def _repo_relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _classify_file(rel_path: str) -> AgentCustomizationAsset | None:
    if rel_path in _EXACT_RULES:
        node_type, platform, name = _EXACT_RULES[rel_path]
        return _asset(rel_path, node_type, platform, name, "exact")

    parts = tuple(Path(rel_path).parts)
    filename = parts[-1] if parts else ""

    if _matches_github_instruction(parts, filename):
        name = _strip_known_suffix(filename)
        return _asset(rel_path, "instruction", "github-copilot", name, "github")
    if _under(parts, ".github", "prompts"):
        name = _strip_known_suffix(filename)
        return _asset(rel_path, "prompt", "github-copilot", name, "github")
    if _under(parts, ".github", "agents"):
        name = _strip_known_suffix(filename)
        return _asset(rel_path, "agent", "github-copilot", name, "github")
    if _is_skill(parts, ".github", "skills"):
        return _asset(
            rel_path,
            "skill",
            "github-copilot",
            parts[-2],
            "github-skill",
        )
    if _under(parts, ".claude", "agents"):
        name = _strip_known_suffix(filename)
        return _asset(rel_path, "agent", "claude", name, "claude-agent")
    if _under(parts, ".claude", "commands"):
        name = _strip_known_suffix(filename)
        return _asset(rel_path, "command", "claude", name, "claude-command")
    if _is_skill(parts, ".claude", "skills"):
        return _asset(rel_path, "skill", "claude", parts[-2], "claude-skill")
    if _under(parts, ".cursor", "rules"):
        name = _strip_known_suffix(filename)
        return _asset(rel_path, "instruction", "cursor", name, "cursor-rule")
    if _under(parts, ".gemini", "agents"):
        name = _strip_known_suffix(filename)
        return _asset(rel_path, "agent", "gemini", name, "gemini-agent")
    if filename == "SKILL.md":
        name = parts[-2] if len(parts) >= 2 else "skill"
        return _asset(rel_path, "skill", "generic", name, "generic-skill")

    return None


def _asset(
    path: str,
    node_type: str,
    platform: str,
    name: str,
    source_kind: str,
) -> AgentCustomizationAsset:
    return AgentCustomizationAsset(
        path=path,
        node_type=node_type,
        name=name,
        platform=platform,
        platform_name=_PLATFORM_LABELS[platform],
        source_kind=source_kind,
    )


def _under(parts: tuple[str, ...], *prefix: str) -> bool:
    return len(parts) > len(prefix) and parts[: len(prefix)] == prefix


def _matches_github_instruction(parts: tuple[str, ...], filename: str) -> bool:
    return (
        _under(parts, ".github", "instructions")
        and filename.endswith(".instructions.md")
    )


def _is_skill(parts: tuple[str, ...], *prefix: str) -> bool:
    return _under(parts, *prefix) and parts[-1] == "SKILL.md" and len(parts) >= 3


def _strip_known_suffix(filename: str) -> str:
    for suffix in _NAME_SUFFIXES:
        if filename.endswith(suffix):
            return filename[: -len(suffix)]
    return Path(filename).stem
