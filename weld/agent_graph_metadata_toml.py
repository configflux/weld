"""TOML config parser for the static Agent Graph (e.g. ``.codex/config.toml``).

Codex's ``[mcp_servers.<name>]`` sections are surfaced here as derived
``mcp-server`` nodes with ``platform=codex`` so they don't collapse onto
the generic node ids minted by ``.mcp.json``. The audit trail in
``docs/agent-graph-real-app-audit.md`` (gap a5) describes the original
silent merge this module fixes.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from weld.agent_graph_metadata_utils import (
    dedupe_references,
    diagnostic,
    iter_strings,
)


def parse_toml_asset(
    root: Path, rel_path: str, platform: str, text: str,
    known_commands: frozenset[str] | None,
) -> Any:
    """Parse a TOML platform config and return a ``ParsedAgentGraphAsset``.

    Imported lazily by the metadata facade to avoid a circular import.
    """
    # Local import: the metadata module imports from this module, and we
    # need ParsedAgentGraphAsset / helpers that live there.
    from weld.agent_graph_metadata import (
        ParsedAgentGraphAsset,
        _broken_file_diagnostics,
        _config_props,
        _mcp_nodes,
        _metadata_references,
        _text_references,
    )

    try:
        payload = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return ParsedAgentGraphAsset(diagnostics=(diagnostic(
            "agent_graph_invalid_toml", rel_path,
            f"Could not parse TOML customization config: {exc}",
        ),))
    if not isinstance(payload, dict):
        return ParsedAgentGraphAsset()

    props = _config_props(payload)
    derived = _mcp_nodes(rel_path, platform, payload)
    refs = list(_metadata_references(payload, line=1, known_commands=known_commands))
    for raw in iter_strings(payload):
        refs.extend(_text_references(raw, start_line=1, known_commands=known_commands))
    refs = dedupe_references(refs)
    diagnostics = _broken_file_diagnostics(root, rel_path, refs)
    for node in derived:
        diagnostics.extend(_broken_file_diagnostics(root, rel_path, node.references))
    return ParsedAgentGraphAsset(
        props=props, references=tuple(dedupe_references(refs)),
        derived_nodes=tuple(derived), diagnostics=tuple(diagnostics),
    )
