"""Java enrichments for the shared tree-sitter strategy."""

from __future__ import annotations

import re

from weld._node_ids import package_id as _canonical_package_id

_SAFE_PACKAGE_RE = re.compile(r"[^0-9A-Za-z_.-]+")
_VISIBILITY_RE = re.compile(r"\b(public|private|protected)\b")


def enrich_file_node(
    nodes: dict[str, dict],
    edges: list[dict],
    file_node_id: str,
    node_props: dict,
    symbols: dict[str, list[str]],
    source_text: str,
    source_strategy: str,
) -> None:
    """Add Java-specific metadata and import dependency nodes."""
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
        ("annotations", "annotations"),
        ("methods", "methods"),
        ("packages", "packages"),
    ):
        values = _dedupe(symbols.get(query_key, []))
        if values:
            node_props[prop_key] = values

    method_visibility = _visibility_map(source_text, node_props.get("methods", []))
    if method_visibility:
        node_props["method_visibility"] = method_visibility

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
    """Create package nodes and depends_on edges for each import."""
    seen: set[str] = set()
    for import_name in imports:
        # Extract the package portion (everything up to the last dot).
        package = _import_to_package(import_name)
        if package in seen:
            continue
        seen.add(package)
        package_id = _package_node_id(package)
        legacy_pid = _legacy_package_id(package)
        aliases = sorted({legacy_pid} - {package_id})
        package_props: dict = {
            "name": package,
            "language": "java",
            "source_strategy": source_strategy,
            "authority": "derived",
            "confidence": "definite",
        }
        if aliases:
            package_props["aliases"] = aliases
        nodes.setdefault(
            package_id,
            {
                "type": "package",
                "label": package,
                "props": package_props,
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
    """Remove duplicates while preserving order."""
    seen: set[str] = set()
    return [v for v in values if v and not (v in seen or seen.add(v))]


def _import_to_package(import_name: str) -> str:
    """Extract the package portion from a fully-qualified import.

    ``com.example.service.UserService`` -> ``com.example.service``
    ``java.util.List`` -> ``java.util``

    If there is no dot, return the import name as-is.
    """
    dot_idx = import_name.rfind(".")
    if dot_idx > 0:
        return import_name[:dot_idx]
    return import_name


def _legacy_package_id(package_name: str) -> str:
    """Pre-ADR-0041 Java package id shape; recorded under ``aliases``."""
    safe = _SAFE_PACKAGE_RE.sub("_", package_name).strip("._")
    return f"package:java:{safe or 'unknown'}"


def _package_node_id(package_name: str) -> str:
    """Canonical Java package id per ADR 0041 (lowercased via ``canonical_slug``)."""
    return _canonical_package_id("java", package_name)


def _visibility_map(
    source_text: str,
    method_names: list[str],
) -> dict[str, list[str]]:
    """Build a mapping of method name -> list of visibility modifiers."""
    result: dict[str, list[str]] = {}
    wanted = set(method_names)
    for line in source_text.splitlines():
        visibility = _visibility(line)
        if not visibility:
            continue
        for name in wanted:
            if not _line_mentions_method(line, name):
                continue
            result.setdefault(name, [])
            if visibility not in result[name]:
                result[name].append(visibility)
    return {name: sorted(values) for name, values in sorted(result.items())}


def _line_mentions_method(line: str, name: str) -> bool:
    """Check whether *line* declares *name* as a method (has parentheses)."""
    if not re.search(rf"\b{re.escape(name)}\b", line):
        return False
    return f"{name}(" in line


def _visibility(line: str) -> str | None:
    """Extract the visibility modifier from a source line."""
    matches = _VISIBILITY_RE.findall(line)
    if not matches:
        return None
    return matches[0]
