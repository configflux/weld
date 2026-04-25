"""Human-readable rendering helpers for Agent Graph CLI payloads."""

from __future__ import annotations

from typing import Any


def print_explanation(explanation: dict[str, Any]) -> None:
    asset = explanation["asset"]
    print(asset["name"])
    print(f"Type: {asset['type']}")
    print(f"Status: {asset['status']}")
    _print_platform_variants(explanation["platform_variants"])
    _print_block("Purpose", [explanation["purpose"]] if explanation["purpose"] else [])
    _print_block("Source files", explanation["source_files"])
    _print_relationships("Outgoing references", explanation["outgoing_references"])
    _print_relationships("Incoming references", explanation["incoming_references"])
    _print_related(explanation["related"])
    _print_overlap(explanation["overlaps"])


def print_impact(impact: dict[str, Any]) -> None:
    asset = impact["asset"]
    print(f"Changing {asset['path'] or asset['id']} affects:")
    _print_node_entries(impact["affected_nodes"])
    print(f"Authority status: {impact['authority_status']}")
    _print_node_entries(impact["same_name_variants"], title="Same-name variants")
    _print_node_entries(impact["same_purpose_variants"], title="Same-purpose variants")
    _print_block("Recommended", impact["change_checklist"])


def print_audit(payload: dict[str, Any]) -> None:
    findings = payload["findings"]
    if not findings:
        print("No Agent Graph audit findings.")
        return
    print("Agent Graph audit findings:")
    for index, finding in enumerate(findings, start=1):
        print(f"{index}. {finding['title']}")
        print(f"   Severity: {finding['severity']}")
        print(f"   Code: {finding['code']}")
        for node in finding.get("nodes", []):
            where = f" at {node['path']}" if node.get("path") else ""
            print(f"   - {node['type']}:{node['name']}{where}")
        if finding.get("message"):
            print(f"   {finding['message']}")


def print_change_plan(payload: dict[str, Any]) -> None:
    print("Change plan")
    print(f"Request: {payload['request']}")
    _print_block("Primary files", payload["primary_files"])
    _print_block("Secondary files", payload["secondary_files"])
    _print_block("Validation files", payload["validation_files"])
    _print_block("Warnings", payload["warnings"])
    _print_block("Validation", payload["validation_steps"])
    _print_node_entries(payload["primary_assets"], title="Primary assets")
    _print_node_entries(payload["secondary_assets"], title="Secondary assets")


def _print_platform_variants(entries: list[dict[str, Any]]) -> None:
    print("Platforms:")
    if not entries:
        print("  - none")
        return
    for entry in entries:
        detail = f": {entry['path']}" if entry["path"] else ""
        print(f"  - {entry['platform_name']}{detail}")


def _print_block(title: str, values: list[str]) -> None:
    print(f"{title}:")
    if not values:
        print("  - none")
        return
    for value in values:
        print(f"  - {value}")


def _print_relationships(title: str, relationships: list[dict[str, Any]]) -> None:
    print(f"{title}:")
    if not relationships:
        print("  - none")
        return
    for relationship in relationships:
        node = relationship["node"]
        print(
            f"  - {relationship['edge_type']} -> "
            f"{node['type']}:{node['name']} ({node['id']})"
        )


def _print_related(related: dict[str, list[dict[str, Any]]]) -> None:
    print("Related:")
    rows = []
    for title, entries in related.items():
        for entry in entries:
            rows.append(f"{title}: {entry['name']} ({entry['id']})")
    _print_block_items(rows)


def _print_overlap(entries: list[dict[str, Any]]) -> None:
    print("Potential overlap:")
    rows = [
        f"{entry['type']}:{entry['name']} ({entry['platform_name']}, {entry['reason']})"
        for entry in entries
    ]
    _print_block_items(rows)


def _print_block_items(values: list[str]) -> None:
    if not values:
        print("  - none")
        return
    for value in sorted(values):
        print(f"  - {value}")


def _print_node_entries(
    entries: list[dict[str, Any]],
    *,
    title: str | None = None,
) -> None:
    if title is not None:
        print(f"{title}:")
    if not entries:
        print("  - none")
        return
    for entry in entries:
        where = f" at {entry['path']}" if entry["path"] else ""
        print(f"  - {entry['type']}:{entry['name']} ({entry['platform_name']}){where}")
