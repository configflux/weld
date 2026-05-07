"""C# origin classification helpers (ADR 0042).

The tree-sitter C# strategy sees ``using`` directives as namespace-like
strings, not compiler-resolved assemblies. These helpers combine that syntax
with SDK project metadata so package nodes can still carry a stable
``props.origin`` value.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Literal
import xml.etree.ElementTree as ET

Origin = Literal["project", "stdlib", "external", "unresolved"]

_CSHARP_STDLIB_ROOTS = frozenset({"System", "Microsoft"})
_NAMESPACE_SAFE_RE = re.compile(r"[^0-9A-Za-z_.]+")


def load_package_references(root: Path) -> frozenset[str]:
    """Return NuGet package IDs declared by ``PackageReference`` entries."""
    package_refs: set[str] = set()
    for project_file in _iter_project_files(root):
        project = _parse_xml(project_file)
        if project is None:
            continue
        for elem in project.iter():
            if _local_name(elem.tag) != "PackageReference":
                continue
            package_name = elem.attrib.get("Include")
            if package_name:
                package_refs.add(package_name.strip())
    return frozenset(p for p in package_refs if p)


def load_project_namespace_roots(root: Path) -> frozenset[str]:
    """Return namespace roots inferred from SDK project metadata."""
    roots: set[str] = set()
    for project_file in _iter_project_files(root):
        roots.add(_namespace_from_project_name(project_file.stem))
        project = _parse_xml(project_file)
        if project is None:
            continue
        for elem in project.iter():
            local = _local_name(elem.tag)
            if local not in {"AssemblyName", "RootNamespace"}:
                continue
            if elem.text and elem.text.strip():
                roots.add(elem.text.strip())
    return frozenset(root_name for root_name in roots if root_name)


def classify_using_import(
    import_name: str,
    *,
    package_references: frozenset[str],
    project_namespace_roots: frozenset[str],
) -> Origin:
    """Return the ADR 0042 origin for a C# ``using`` import."""
    namespace = import_name.strip()
    if not namespace:
        return "unresolved"
    if _matches_any_prefix(namespace, package_references, case_sensitive=False):
        return "external"
    if _namespace_root(namespace) in _CSHARP_STDLIB_ROOTS:
        return "stdlib"
    if _matches_any_prefix(namespace, project_namespace_roots, case_sensitive=True):
        return "project"
    return "unresolved"


def _iter_project_files(root: Path) -> list[Path]:
    try:
        return sorted(root.rglob("*.csproj"))
    except OSError:
        return []


def _parse_xml(path: Path) -> ET.Element | None:
    try:
        return ET.parse(path).getroot()
    except (ET.ParseError, OSError, UnicodeDecodeError):
        return None


def _local_name(tag: Any) -> str:
    if not isinstance(tag, str):
        return ""
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _matches_any_prefix(
    namespace: str,
    prefixes: frozenset[str],
    *,
    case_sensitive: bool,
) -> bool:
    if case_sensitive:
        return any(namespace == p or namespace.startswith(f"{p}.") for p in prefixes)
    folded = namespace.casefold()
    return any(
        folded == p.casefold() or folded.startswith(f"{p.casefold()}.")
        for p in prefixes
    )


def _namespace_root(namespace: str) -> str:
    return namespace.split(".", 1)[0]


def _namespace_from_project_name(name: str) -> str:
    return _NAMESPACE_SAFE_RE.sub("_", name).strip("._")


__all__ = [
    "Origin",
    "classify_using_import",
    "load_package_references",
    "load_project_namespace_roots",
]
