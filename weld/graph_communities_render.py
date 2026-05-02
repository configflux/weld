"""Render and persist graph-community reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from weld.workspace_state import atomic_write_text

COMMUNITIES_JSON = "graph-communities.json"
COMMUNITY_REPORT = "graph-community-report.md"
COMMUNITY_INDEX = "graph-community-index.md"


def dumps_communities(payload: Mapping[str, Any]) -> str:
    """Return stable JSON text for a communities payload."""
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def render_community_report(payload: Mapping[str, Any]) -> str:
    """Render the human report markdown for a communities payload."""
    summary = payload["summary"]
    lines = [
        "# Graph Community Report",
        "",
        (
            f"{summary['total_nodes']} nodes, {summary['valid_edges']} valid edges, "
            f"{summary['total_communities']} communities. Reporting top "
            f"{summary['reported_communities']}."
        ),
        "",
        "## Health",
    ]
    health = payload.get("health") or {}
    lines.extend([
        f"- Stale graph: {_yes_no((health.get('stale_graph') or {}).get('stale'))}",
        f"- Isolated nodes: {(health.get('isolated_nodes') or {}).get('count', 0)}",
        f"- Dangling edges: {(health.get('dangling_edges') or {}).get('count', 0)}",
        f"- Oversized communities: {len(health.get('oversized_communities') or [])}",
        (
            "- Low description coverage: "
            f"{(health.get('description_coverage') or {}).get('coverage_pct', 0.0)}%"
        ),
        (
            "- High-boundary communities: "
            f"{len(health.get('high_boundary_communities') or [])}"
        ),
        "",
    ])
    hubs = payload.get("hubs") or []
    if hubs:
        lines.append("## Hubs")
        lines.append("")
        for hub in hubs:
            lines.append(
                f"- `{hub['id']}` ({hub['type']}, degree {hub['degree']}, {hub['community']})"
            )
        lines.append("")
    for community in payload.get("communities") or []:
        lines.extend(_community_report_lines(community))
    return "\n".join(lines).rstrip() + "\n"


def render_community_index(payload: Mapping[str, Any]) -> str:
    """Render a compact community index markdown table."""
    lines = [
        "# Graph Community Index",
        "",
        "| Community | Nodes | Languages | Types | Internal | Boundary | Hubs |",
        "|---|---:|---|---|---:|---:|---|",
    ]
    for community in payload.get("communities") or []:
        hubs = ", ".join(f"`{n['id']}`" for n in community.get("hub_nodes", [])[:3])
        lines.append(
            "| {id} | {size} | {langs} | {types} | {internal} | {boundary} | {hubs} |".format(
                id=_escape_cell(community["id"]),
                size=community["size"],
                langs=_escape_cell(_top_counts(community.get("languages") or {})),
                types=_escape_cell(_top_counts(community.get("node_types") or {})),
                internal=community["internal_edges"],
                boundary=community["boundary_edges"],
                hubs=hubs,
            )
        )
    return "\n".join(lines).rstrip() + "\n"


def write_community_artifacts(
    output_dir: Path,
    payload: Mapping[str, Any],
) -> dict[str, str]:
    """Persist JSON, report, and index artifacts under ``output_dir``."""
    files = {
        COMMUNITIES_JSON: dumps_communities(payload),
        COMMUNITY_REPORT: render_community_report(payload),
        COMMUNITY_INDEX: render_community_index(payload),
    }
    written: dict[str, str] = {}
    for name, text in files.items():
        path = output_dir / name
        atomic_write_text(path, text)
        written[name] = str(path)
    return written


def _community_report_lines(community: Mapping[str, Any]) -> list[str]:
    lines = [
        f"## {community['id']} - {community['title']}",
        "",
        (
            f"- Nodes: {community['size']}; internal edges: "
            f"{community['internal_edges']}; boundary edges: {community['boundary_edges']}"
        ),
        f"- Dominant languages: {_top_counts(community.get('languages') or {})}",
        f"- Dominant types: {_top_counts(community.get('node_types') or {})}",
        "- Hub nodes:",
    ]
    for node in community.get("hub_nodes") or []:
        lines.append(f"  - `{node['id']}` ({node['type']}, degree {node['degree']})")
    if community.get("key_files"):
        lines.append("- Key files:")
        for item in community["key_files"]:
            lines.append(f"  - `{item['file']}` ({item['nodes']} nodes)")
    if community.get("boundary_links"):
        lines.append("- Cross-community links:")
        for link in community["boundary_links"]:
            lines.append(
                f"  - {link['other_community']}: `{link['from']}` "
                f"--{link['type']}--> `{link['to']}`"
            )
    lines.append("")
    return lines


def _top_counts(counts: Mapping[str, int], *, cap: int = 3) -> str:
    items = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:cap]
    return ", ".join(f"{name} ({count})" for name, count in items) if items else "none"


def _yes_no(value: object) -> str:
    return "yes" if value else "no"


def _escape_cell(value: str) -> str:
    return value.replace("|", "\\|")
