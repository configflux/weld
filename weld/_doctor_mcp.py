"""Doctor check for MCP server configuration presence.

Carved out of :mod:`weld.doctor` to keep the dispatcher under the
400-line CLAUDE.md cap, matching every other doctor concern already
split into its own file -- a pure move, no behavior change: existing
callers of ``weld.doctor.doctor()`` see the identical `[MCP]` output.

Neither `.mcp.json` nor `.codex/config.toml` is required, so absence is
a `note` (dismissable via ``wd doctor --ack mcp-config-missing``), never
a `warn` or `fail`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def check_mcp_config(root: Path, result_cls: type[Any]) -> list[Any]:
    """Return whether MCP server config is present at *root*."""
    repo_mcp = root / ".mcp.json"
    codex_mcp = root / ".codex" / "config.toml"

    found: list[str] = []
    if repo_mcp.is_file():
        found.append(".mcp.json")
    if codex_mcp.is_file():
        found.append(".codex/config.toml")

    if found:
        locations = " and ".join(found)
        return [result_cls("ok", f"MCP server config found in {locations}", "MCP")]
    return [
        result_cls(
            "note",
            "MCP server config not found (.mcp.json or .codex/config.toml)",
            "MCP",
            note_id="mcp-config-missing",
        )
    ]


__all__ = ["check_mcp_config"]
