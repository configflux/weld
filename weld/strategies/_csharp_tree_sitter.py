"""C# enrichments for the shared tree-sitter strategy."""

from __future__ import annotations

from pathlib import Path
import re

from weld._node_ids import package_id as _canonical_package_id

_SAFE_PACKAGE_RE = re.compile(r"[^0-9A-Za-z_.-]+")
_VISIBILITY_RE = re.compile(r"\b(public|private|protected|internal)\b")
_STARTUP_MARKERS = (
    "WebApplication.CreateBuilder",
    "Host.CreateDefaultBuilder",
    "CreateHostBuilder",
    "builder.Build(",
    "app.Run(",
    "static void Main",
    "static async Task Main",
)


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
    rel_path = str(node_props.get("file") or "")
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
    if is_startup_source(rel_path, source_text, symbols):
        _add_startup_nodes(
            nodes,
            edges,
            rel_path,
            node_props.get("imports_from", []),
            source_text,
            source_strategy,
        )


def is_startup_source(rel_path: str, source_text: str, symbols: dict[str, list[str]]) -> bool:
    """Return True when a C# file is likely to be an application startup file."""
    if Path(rel_path).name.lower() == "program.cs":
        return True
    if "Program" in symbols.get("classes", []):
        return True
    return any(marker in source_text for marker in _STARTUP_MARKERS)


def _add_startup_nodes(
    nodes: dict[str, dict],
    edges: list[dict],
    rel_path: str,
    imports: list[str],
    source_text: str,
    source_strategy: str,
) -> None:
    base = Path(rel_path).with_suffix("").as_posix()
    entrypoint_id = f"entrypoint:{base}"
    boundary_id = f"boundary:{base}:host"
    kind, framework = _startup_kind(source_text, imports)
    nodes.setdefault(
        entrypoint_id,
        {
            "type": "entrypoint",
            "label": Path(rel_path).stem,
            "props": {
                "file": rel_path,
                "kind": kind,
                "framework": framework,
                "language": "csharp",
                "source_strategy": source_strategy,
                "authority": "derived",
                "confidence": "inferred",
                "roles": ["implementation"],
                "description": (
                    "C#/.NET runtime startup entrypoint for application "
                    "execution flow."
                ),
            },
        },
    )
    nodes.setdefault(
        boundary_id,
        {
            "type": "boundary",
            "label": f"{Path(rel_path).stem} host",
            "props": {
                "file": rel_path,
                "kind": "runtime_host",
                "framework": framework,
                "language": "csharp",
                "source_strategy": source_strategy,
                "authority": "derived",
                "confidence": "inferred",
                "roles": ["implementation"],
                "description": (
                    "C#/.NET runtime host boundary that starts the application."
                ),
            },
        },
    )
    edges.append(_edge(boundary_id, entrypoint_id, "exposes", source_strategy))
    service_id = _owning_service_id(rel_path)
    if service_id:
        nodes.setdefault(service_id, _service_node(service_id, source_strategy))
        edges.append(_edge(service_id, entrypoint_id, "contains", source_strategy))
        edges.append(_edge(service_id, boundary_id, "contains", source_strategy))
    for import_name in imports:
        edges.append(
            _edge(entrypoint_id, _package_node_id(import_name), "depends_on", source_strategy)
        )


def _startup_kind(source_text: str, imports: list[str]) -> tuple[str, str]:
    joined_imports = " ".join(imports)
    if "AspNetCore" in joined_imports or "WebApplication.CreateBuilder" in source_text:
        return "web_host", "aspnetcore"
    if "Microsoft.Extensions.Hosting" in joined_imports or "Host.CreateDefaultBuilder" in source_text:
        return "generic_host", "dotnet"
    return "main", "dotnet"


def _owning_service_id(rel_path: str) -> str | None:
    parts = Path(rel_path).parts
    if len(parts) >= 2 and parts[0] == "services":
        return f"service:{parts[1]}"
    return None


def _service_node(service_id: str, source_strategy: str) -> dict:
    service_name = service_id.split(":", 1)[1]
    return {
        "type": "service",
        "label": f"{service_name} service",
        "props": {
            "language": "csharp",
            "source_strategy": source_strategy,
            "authority": "derived",
            "confidence": "inferred",
            "roles": ["implementation"],
            "description": (
                "Runtime service containing .NET startup and host boundaries."
            ),
        },
    }


def _edge(src: str, dst: str, edge_type: str, source_strategy: str) -> dict:
    return {
        "from": src,
        "to": dst,
        "type": edge_type,
        "props": {
            "source_strategy": source_strategy,
            "confidence": "inferred",
        },
    }


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
        legacy_pid = _legacy_package_id(import_name)
        aliases = sorted({legacy_pid} - {package_id})
        package_props: dict = {
            "name": import_name,
            "language": "csharp",
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
                "label": import_name,
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
    seen: set[str] = set()
    return [v for v in values if v and not (v in seen or seen.add(v))]


def _legacy_package_id(import_name: str) -> str:
    """Pre-ADR-0041 C# package id shape; recorded under ``aliases``."""
    safe = _SAFE_PACKAGE_RE.sub("_", import_name).strip("._")
    return f"package:csharp:{safe or 'unknown'}"


def _package_node_id(import_name: str) -> str:
    """Canonical C# package id per ADR 0041 (lowercased via ``canonical_slug``)."""
    return _canonical_package_id("csharp", import_name)


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
