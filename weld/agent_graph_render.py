"""Human-readable rendering helpers for Agent Graph CLI payloads.

Every value rendered here -- asset names, node ids, file paths, finding
titles -- is read out of the scanned repository's ``.claude/`` tree and its
frontmatter, so in a hostile repo it is attacker-chosen. This module used to
emit it one ``print()`` at a time, which left no single expression to escape.

It is now split the way the rest of the CLI is: ``format_*`` functions are
pure and return the rendered block, and each ``print_*`` entry point is the
one write boundary, passing that block through
:func:`weld._safe_text.sanitize_terminal_text`. Keeping the write direct --
rather than funnelling it through a local helper -- is what lets
``tools/lint_terminal_safety.py`` verify the escape structurally.
"""

from __future__ import annotations

import sys
from collections import Counter
from typing import Any

from weld._safe_text import sanitize_terminal_text


def _emit(text: str) -> None:
    """The single write boundary for this module."""
    sys.stdout.write(sanitize_terminal_text(text))


def _block(lines: list[str]) -> str:
    """Join rendered *lines* the way a run of ``print()`` calls would."""
    return "".join(line + "\n" for line in lines)


def format_explanation(explanation: dict[str, Any]) -> str:
    asset = explanation["asset"]
    lines = [
        asset["name"],
        f"Type: {asset['type']}",
        f"Status: {asset['status']}",
    ]
    lines += _canonical_rendered_lines(explanation)
    lines += _platform_variant_lines(explanation["platform_variants"])
    lines += _block_lines(
        "Purpose", [explanation["purpose"]] if explanation["purpose"] else [],
    )
    lines += _block_lines("Source files", explanation["source_files"])
    lines += _relationship_lines(
        "Outgoing references", explanation["outgoing_references"],
    )
    lines += _relationship_lines(
        "Incoming references", explanation["incoming_references"],
    )
    lines += _related_lines(explanation["related"])
    lines += _overlap_lines(explanation["overlaps"])
    return _block(lines)


def print_explanation(explanation: dict[str, Any]) -> None:
    _emit(format_explanation(explanation))


def _canonical_rendered_lines(explanation: dict[str, Any]) -> list[str]:
    """ADR 0029: surface canonical->rendered relationship explicitly."""
    canonical = explanation.get("canonical_source")
    rendered = explanation.get("rendered_targets") or []
    lines: list[str] = []
    if canonical:
        node = canonical["node"]
        where = f" at {node['path']}" if node.get("path") else ""
        lines.append(f"Canonical source: {node['name']}{where}")
    if rendered:
        lines.append("Rendered targets:")
        for rel in rendered:
            node = rel["node"]
            where = f" at {node['path']}" if node.get("path") else ""
            lines.append(f"  - {node['type']}:{node['name']}{where}")
    return lines


def format_impact(impact: dict[str, Any]) -> str:
    asset = impact["asset"]
    lines = [f"Changing {asset['path'] or asset['id']} affects:"]
    lines += _node_entry_lines(impact["affected_nodes"])
    lines.append(f"Authority status: {impact['authority_status']}")
    lines += _node_entry_lines(
        impact["same_name_variants"], title="Same-name variants",
    )
    lines += _node_entry_lines(
        impact["same_purpose_variants"], title="Same-purpose variants",
    )
    lines += _block_lines("Recommended", impact["change_checklist"])
    return _block(lines)


def print_impact(impact: dict[str, Any]) -> None:
    _emit(format_impact(impact))


def format_audit(payload: dict[str, Any]) -> str:
    findings = payload["findings"]
    if not findings:
        return _block(["No Agent Graph audit findings."])
    lines = ["Agent Graph audit findings:"]
    for index, finding in enumerate(findings, start=1):
        lines.append(f"{index}. {finding['title']}")
        lines.append(f"   Severity: {finding['severity']}")
        lines.append(f"   Code: {finding['code']}")
        for node in finding.get("nodes", []):
            where = f" at {node['path']}" if node.get("path") else ""
            lines.append(f"   - {node['type']}:{node['name']}{where}")
        if finding.get("message"):
            lines.append(f"   {finding['message']}")
    return _block(lines)


def print_audit(payload: dict[str, Any]) -> None:
    _emit(format_audit(payload))


def format_change_plan(payload: dict[str, Any]) -> str:
    lines = ["Change plan", f"Request: {payload['request']}"]
    lines += _block_lines("Primary files", payload["primary_files"])
    lines += _block_lines("Secondary files", payload["secondary_files"])
    lines += _block_lines("Validation files", payload["validation_files"])
    lines += _block_lines("Warnings", payload["warnings"])
    lines += _block_lines("Validation", payload["validation_steps"])
    lines += _node_entry_lines(payload["primary_assets"], title="Primary assets")
    lines += _node_entry_lines(
        payload["secondary_assets"], title="Secondary assets",
    )
    return _block(lines)


def print_change_plan(payload: dict[str, Any]) -> None:
    _emit(format_change_plan(payload))


def _platform_variant_lines(entries: list[dict[str, Any]]) -> list[str]:
    lines = ["Platforms:"]
    if not entries:
        return lines + ["  - none"]
    for entry in entries:
        detail = f": {entry['path']}" if entry["path"] else ""
        lines.append(f"  - {entry['platform_name']}{detail}")
    return lines


def _block_lines(title: str, values: list[str]) -> list[str]:
    lines = [f"{title}:"]
    if not values:
        return lines + ["  - none"]
    return lines + [f"  - {value}" for value in values]


def _relationship_lines(
    title: str, relationships: list[dict[str, Any]],
) -> list[str]:
    lines = [f"{title}:"]
    if not relationships:
        return lines + ["  - none"]
    for relationship in relationships:
        node = relationship["node"]
        lines.append(
            f"  - {relationship['edge_type']} -> "
            f"{node['type']}:{node['name']} ({node['id']})"
        )
    return lines


def _related_lines(related: dict[str, list[dict[str, Any]]]) -> list[str]:
    rows = []
    for title, entries in related.items():
        for entry in entries:
            rows.append(f"{title}: {entry['name']} ({entry['id']})")
    return ["Related:"] + _sorted_item_lines(rows)


def _overlap_lines(entries: list[dict[str, Any]]) -> list[str]:
    rows = [
        f"{entry['type']}:{entry['name']} ({entry['platform_name']}, {entry['reason']})"
        for entry in entries
    ]
    return ["Potential overlap:"] + _sorted_item_lines(rows)


def _sorted_item_lines(values: list[str]) -> list[str]:
    if not values:
        return ["  - none"]
    return [f"  - {value}" for value in sorted(values)]


def _node_entry_lines(
    entries: list[dict[str, Any]],
    *,
    title: str | None = None,
) -> list[str]:
    lines = [f"{title}:"] if title is not None else []
    if not entries:
        return lines + ["  - none"]
    for entry in entries:
        where = f" at {entry['path']}" if entry["path"] else ""
        lines.append(
            f"  - {entry['type']}:{entry['name']} "
            f"({entry['platform_name']}){where}"
        )
    return lines


# --- command-level blocks --------------------------------------------------
#
# ``list`` / ``discover`` build their output the same way, and they render the
# same untrusted material (asset names, declared paths, diagnostic messages
# quoting frontmatter). They live here so :mod:`weld.agent_graph_cli` keeps
# one sanitized write per command instead of a run of bare ``print`` calls.


def format_asset_list(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return "No Agent Graph assets found.\n"
    out: list[str] = []
    current_platform: str | None = None
    for entry in entries:
        if entry["platform_name"] != current_platform:
            if current_platform is not None:
                out.append("\n")
            current_platform = entry["platform_name"]
            out.append(current_platform + "\n")
        out.append(_asset_row(entry) + "\n")
    return "".join(out)


def _asset_row(entry: dict[str, Any]) -> str:
    description = entry["description"]
    suffix = f" - {description}" if description else ""
    return (
        f"  {entry['type']:<12} {entry['name']:<24} "
        f"{entry['path']} [{entry['status']}]{suffix}"
    ).rstrip()


def format_discover_summary(
    graph: dict[str, Any],
    *,
    root_display: str,
    write_display: str | None,
) -> str:
    """Render the ``discover`` summary block.

    Path *resolution* stays in the CLI; this takes the already-displayed
    strings so the formatter is pure text. ``write_display`` is ``None`` when
    ``--no-write`` suppressed the write.
    """
    meta = graph.get("meta", {})
    diagnostics = meta.get("diagnostics") or []
    discovered_from = meta.get("discovered_from") or []
    lines = [
        "Agent Graph discovery",
        f"Root: {root_display}",
        f"Assets: {len(discovered_from)}",
        f"Nodes: {len(graph.get('nodes', {}))}",
        f"Edges: {len(graph.get('edges', []))}",
        _diagnostics_summary(diagnostics),
        "Write: skipped (--no-write)"
        if write_display is None
        else f"Write: {write_display}",
    ]
    return _block(lines)


def _diagnostics_summary(diagnostics: list[dict[str, Any]]) -> str:
    total = len(diagnostics)
    if total == 0:
        return "Diagnostics: 0"
    counts = Counter(d.get("code") or "<unknown>" for d in diagnostics)
    breakdown = ", ".join(
        f"{count} {code}" for code, count in counts.most_common()
    )
    return f"Diagnostics: {total} ({breakdown})"


def format_diagnostic_list(diagnostics: list[dict[str, Any]]) -> str:
    out: list[str] = []
    for diag in diagnostics:
        severity = diag.get("severity") or "warning"
        code = diag.get("code") or "<unknown>"
        path = diag.get("path") or "<unknown>"
        line = diag.get("line")
        loc = f"{path}:{line}" if line is not None else path
        message = diag.get("message") or ""
        out.append(f"  {severity} {code} {loc} - {message}".rstrip() + "\n")
    return "".join(out)
