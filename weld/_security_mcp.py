"""MCP-side checks for the trust-posture engine (ADR 0025).

Factored out of ``weld/_security_posture.py`` to keep that module under the
400-line cap. These helpers verify that ``weld.mcp_server`` is importable,
that the MCP graph-backed tools have something to read, and that the
``.mcp.json`` server registration is parseable. They never echo commands or
environment values from ``.mcp.json``; only server *names* are surfaced so
the output stays safe to paste into bug reports.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

from weld._mcp_guard import graph_present


_SECTION_MCP = "MCP"


def check_mcp_importable(signal_cls: type):
    """Return a signal indicating whether ``weld.mcp_server`` imports.

    ``signal_cls`` is :class:`weld._security_posture.Signal` -- passed in to
    avoid a circular import.
    """
    try:
        importlib.import_module("weld.mcp_server")
    except Exception as exc:  # noqa: BLE001 -- importlib can raise widely
        return signal_cls(
            id="mcp_importable",
            level="warn",
            section=_SECTION_MCP,
            message=(
                "weld.mcp_server is not importable -- MCP clients cannot "
                f"start the server ({exc.__class__.__name__})"
            ),
            details={"error": exc.__class__.__name__},
        )
    return signal_cls(
        id="mcp_importable",
        level="ok",
        section=_SECTION_MCP,
        message="weld.mcp_server imports cleanly",
    )


def check_mcp_graph_ready(root: Path, signal_cls: type):
    """Return a signal indicating whether MCP graph-backed tools have data."""
    if graph_present(root):
        return signal_cls(
            id="mcp_graph_present",
            level="ok",
            section=_SECTION_MCP,
            message="MCP graph-backed tools have a graph or workspaces.yaml",
        )
    return signal_cls(
        id="mcp_graph_present",
        level="warn",
        section=_SECTION_MCP,
        message=(
            "no .weld/graph.json and no workspaces.yaml -- MCP graph-backed "
            "tools will return graph_missing payloads (run: wd discover)"
        ),
    )


def check_mcp_config(root: Path, signal_cls: type):
    """Return a signal for ``.mcp.json`` posture, or None if absent.

    Lists only server *names*; never echoes commands or env entries.
    """
    config = root / ".mcp.json"
    if not config.is_file():
        return None
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except Exception:
        return signal_cls(
            id="mcp_config_unreadable",
            level="warn",
            section=_SECTION_MCP,
            message=".mcp.json present but not parseable",
        )
    servers = data.get("mcpServers") or data.get("servers") or {}
    if not isinstance(servers, dict):
        return None
    names = sorted(servers.keys())
    extra = [n for n in names if n != "weld"]
    if not extra:
        return signal_cls(
            id="mcp_config_servers",
            level="ok",
            section=_SECTION_MCP,
            message=(
                f".mcp.json registers {len(names)} server(s): "
                f"{', '.join(names) if names else '(none)'}"
            ),
            details={"servers": names},
        )
    return signal_cls(
        id="mcp_config_servers",
        level="warn",
        section=_SECTION_MCP,
        message=(
            ".mcp.json registers external MCP servers: "
            f"{', '.join(extra)} -- review their commands before launch"
        ),
        details={"servers": names, "external": extra},
    )
