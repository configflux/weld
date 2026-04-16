"""C# enrichments for the shared tree-sitter strategy."""

from __future__ import annotations

import re

_SAFE_PACKAGE_RE = re.compile(r"[^0-9A-Za-z_.-]+")
_VISIBILITY_RE = re.compile(r"\b(public|private|protected|internal)\b")


def enrich_file_node(
    nodes: dict[str, dict],
    edges: list[dict],
    file_node_id: str,
    node_props: dict,
    symbols: dict[str, list[str]],
    source_text: str,
    source_strategy: str,
) -> None:
    """Add C#-specific metadata and import dependency nodes."""
    for key in ("exports", "classes", "imports"):
        if key in symbols:
            symbols[key] = _dedupe(symbols[key])
    if symbols.get("exports"):
        node_props["exports"] = symbols["exports"]
    if symbols.get("classes"):
        node_props["types"] = symbols["classes"]
    if symbols.get("imports"):
        node_props["imports_from"] = symbols["imports"]

    for query_key, prop_key in (
        ("attributes", "attributes"),
        ("methods", "methods"),
        ("namespaces", "namespaces"),
        ("properties", "properties"),
    ):
        values = _dedupe(symbols.get(query_key, []))
        if values:
            node_props[prop_key] = values

    method_visibility = _visibility_map(
        source_text,
        node_props.get("methods", []),
        requires_call=True,
    )
    property_visibility = _visibility_map(
        source_text,
        node_props.get("properties", []),
        requires_call=False,
    )
    if method_visibility:
        node_props["method_visibility"] = method_visibility
    if property_visibility:
        node_props["property_visibility"] = property_visibility

    _add_import_dependencies(
        nodes,
        edges,
        file_node_id,
        node_props.get("imports_from", []),
        source_strategy,
    )


def _add_import_dependencies(
    nodes: dict[str, dict],
    edges: list[dict],
    file_node_id: str,
    imports: list[str],
    source_strategy: str,
) -> None:
    seen: set[str] = set()
    for import_name in imports:
        if import_name in seen:
            continue
        seen.add(import_name)
        package_id = _package_node_id(import_name)
        nodes.setdefault(
            package_id,
            {
                "type": "package",
                "label": import_name,
                "props": {
                    "name": import_name,
                    "language": "csharp",
                    "source_strategy": source_strategy,
                    "authority": "derived",
                    "confidence": "definite",
                },
            },
        )
        edges.append(
            {
                "from": file_node_id,
                "to": package_id,
                "type": "depends_on",
                "props": {
                    "import_name": import_name,
                    "source_strategy": source_strategy,
                    "confidence": "definite",
                },
            }
        )


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    return [v for v in values if v and not (v in seen or seen.add(v))]


def _package_node_id(import_name: str) -> str:
    safe = _SAFE_PACKAGE_RE.sub("_", import_name).strip("._")
    return f"package:csharp:{safe or 'unknown'}"


def _visibility_map(
    source_text: str,
    names: list[str],
    *,
    requires_call: bool,
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    wanted = set(names)
    for line in source_text.splitlines():
        visibility = _visibility(line)
        if not visibility:
            continue
        for name in wanted:
            if not _line_mentions_member(line, name, requires_call=requires_call):
                continue
            result.setdefault(name, [])
            if visibility not in result[name]:
                result[name].append(visibility)
    return {name: sorted(values) for name, values in sorted(result.items())}


def _line_mentions_member(line: str, name: str, *, requires_call: bool) -> bool:
    if not re.search(rf"\b{re.escape(name)}\b", line):
        return False
    if requires_call:
        return f"{name}(" in line
    return "{" in line or "=>" in line


def _visibility(line: str) -> str | None:
    matches = _VISIBILITY_RE.findall(line)
    if not matches:
        return None
    if "protected" in matches and "internal" in matches:
        return "protected internal"
    return matches[0]
