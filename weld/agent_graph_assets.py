"""Leaf primitives shared by ``agent_graph_inventory`` and
``agent_graph_render_pairs``.

Extracted to break a circular import: ``render_pairs`` needs
``ASSET_NODE_TYPES`` and ``node_entry`` to bridge canonical/derived
relationships; ``inventory.explain_asset`` in turn calls
``render_pair_partners`` on the constructed entry. Putting both
primitives here lets each consumer import directly from the leaf,
which means ``inventory`` can import ``render_pairs`` at the top of
the module instead of inside the function body.
"""

from __future__ import annotations

from typing import Any

ASSET_NODE_TYPES = frozenset({
    "agent",
    "command",
    "config",
    "hook",
    "instruction",
    "mcp-server",
    "prompt",
    "skill",
    "subagent",
    "workflow",
})


def single_line(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def asset_status(props: dict[str, Any]) -> str:
    authority = props.get("authority")
    if authority is True:
        return "canonical"
    if authority == "canonical":
        return "canonical"
    if props.get("generated") is True:
        return "generated"
    # ADR 0041: ``external`` and ``referenced`` are the registry's
    # default authority levels for asset-discovered and reference-only
    # nodes. They represent the historical workflow ``status`` axis
    # (``manual`` / ``referenced``) rather than a frontmatter-declared
    # authority claim, so callers that ask for the asset status keep
    # the legacy values.
    if authority and authority not in {"external", "referenced"}:
        return str(authority)
    status = props.get("status")
    return str(status) if status else "manual"


def node_entry(node_id: str, node: dict[str, Any]) -> dict[str, Any]:
    """Return a stable display entry for any graph node."""
    props = node.get("props") if isinstance(node.get("props"), dict) else {}
    platform = str(
        props.get("platform") or props.get("source_platform") or "generic",
    )
    platform_name = str(props.get("platform_name") or platform)
    name = str(props.get("name") or node.get("label") or node_id)
    return {
        "description": single_line(props.get("description")),
        "id": node_id,
        "name": name,
        "path": str(props.get("file") or ""),
        "platform": platform,
        "platform_name": platform_name,
        "status": asset_status(props),
        "type": str(node.get("type") or ""),
    }
