"""Strategy: C# project file (.csproj) extraction (ADR 0056 Wave 1).

Parses ``.csproj`` files for two graph-shaping facts:

1. ``<ProjectReference Include="..\\Foo\\Foo.csproj" />`` entries become
   ``csproj://<project> -> depends_on -> csproj://<referenced-project>``
   edges. The destination project id is derived from the *referenced
   file stem* so the edge resolves whether or not the referenced project
   sits inside the discovery root.

2. ``Directory.Build.props`` and ``Directory.Build.targets`` walk up the
   directory tree (per MSBuild semantics) and inherit
   ``TargetFramework`` / ``RootNamespace`` / ``AssemblyName`` /
   ``LangVersion`` / ``Nullable`` properties. Properties declared in
   the project file itself win over inherited ones.

The strategy is XML-parsed, so every edge ships with
``confidence="definite"`` per ADR 0050. ``PackageReference`` parsing
already lives in :mod:`weld.strategies._csharp_origin`; this strategy
intentionally does not duplicate it -- the origin-classification path
loads packages independently to seed the using-resolver.

Wave 2 of ADR 0056 layers framework-aware extraction (ASP.NET routes,
EF Core entities, test-framework detection) on top of the project
graph this strategy emits. The fixture under
``weld/tests/fixtures/csharp_project/`` therefore carries the Wave 2
seams (controller attributes, DbContext, xUnit ``[Fact]`` markers)
even though only the project-graph slice is asserted here.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from weld.strategies._helpers import (
    StrategyResult,
    filter_glob_results,
    should_skip,
)

#: Properties we surface on project nodes when declared in the csproj
#: or inherited from ``Directory.Build.props/targets``. Selecting a
#: small set keeps the node payload focused; new properties land here
#: only with a downstream consumer in mind.
_INHERITED_PROPERTIES: tuple[str, ...] = (
    "TargetFramework",
    "TargetFrameworks",
    "RootNamespace",
    "AssemblyName",
    "LangVersion",
    "Nullable",
    "OutputType",
)

#: Filenames probed when walking up the directory tree for inherited
#: MSBuild properties. ``props`` is read first, then ``targets`` -- the
#: latter overrides the former, matching MSBuild's evaluation order.
_DIRECTORY_BUILD_FILES: tuple[str, ...] = (
    "Directory.Build.props",
    "Directory.Build.targets",
)


def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract csproj nodes and their ProjectReference edges."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source.get("glob", "**/*.csproj")
    excludes = source.get("exclude", [])

    matched = _resolve_glob(root, pattern)
    matched = filter_glob_results(root, matched)

    for project_file in matched:
        if not project_file.is_file():
            continue
        if should_skip(project_file, excludes):
            continue

        rel_path = project_file.relative_to(root).as_posix()
        discovered_from.append(rel_path)

        project_name = project_file.stem
        nid = _csproj_id(project_name)
        inherited = _load_inherited_properties(project_file, root)
        own_props, references = _parse_project(project_file)
        # Inherited values are the floor; values declared on the csproj
        # itself win. This matches MSBuild's last-write-wins semantics
        # for nested ``PropertyGroup`` declarations.
        merged: dict[str, str] = {**inherited, **own_props}

        nodes[nid] = {
            "type": "build-target",
            "label": project_name,
            "props": {
                "file": rel_path,
                "project_name": project_name,
                "source_strategy": "csharp_project",
                "authority": "canonical",
                "confidence": "definite",
                "roles": ["build"],
                **{k.lower(): v for k, v in merged.items()},
            },
        }

        # Emit one ``depends_on`` edge per ProjectReference, sorted for
        # determinism so the JSON output is byte-stable across runs on
        # the same input.
        for ref_name in sorted(references):
            ref_id = _csproj_id(ref_name)
            edges.append({
                "from": nid,
                "to": ref_id,
                "type": "depends_on",
                "props": {
                    "source_strategy": "csharp_project",
                    "confidence": "definite",
                },
            })

    return StrategyResult(nodes, edges, discovered_from)


def _resolve_glob(root: Path, pattern: str) -> list[Path]:
    """Expand *pattern* against *root* into a sorted file list.

    Mirrors :func:`weld.strategies.bazel.extract`'s glob handling: a
    ``**`` pattern walks recursively, otherwise the pattern is
    treated as a single-directory wildcard. Sorting keeps the
    discovery output deterministic.
    """
    if "**" in pattern:
        return sorted(root.glob(pattern))
    parent = (root / pattern).parent
    if not parent.is_dir():
        return []
    return sorted(parent.glob(Path(pattern).name))


def _csproj_id(project_name: str) -> str:
    """Return the canonical ``csproj://<name>`` ID.

    The ID uses the original casing from the project filename. We do
    not slug-fold here because the same project name is reused
    by-reference across .sln and .csproj files; collapsing the case
    would break the ``ProjectReference`` resolution that follows.
    """
    return f"csproj://{project_name}"


def _parse_project(path: Path) -> tuple[dict[str, str], list[str]]:
    """Return (own_props, project_reference_names) for *path*.

    Malformed XML is treated as an empty project so a single bad file
    does not crash discovery. ``ProjectReference`` paths are split on
    both forward and backward slashes (Windows-style paths are normal
    in .csproj files) and only the stem (basename without extension)
    is returned, so the resulting csproj IDs collide with whatever
    ``.csproj`` file actually defines that project.
    """
    root_elem = _parse_xml(path)
    if root_elem is None:
        return {}, []

    own_props: dict[str, str] = {}
    references: list[str] = []
    for elem in root_elem.iter():
        local = _local_name(elem.tag)
        if local in _INHERITED_PROPERTIES and elem.text and elem.text.strip():
            own_props[local] = elem.text.strip()
        elif local == "ProjectReference":
            include = elem.attrib.get("Include", "").strip()
            ref_name = _project_name_from_reference(include)
            if ref_name:
                references.append(ref_name)
    return own_props, references


def _project_name_from_reference(include: str) -> str:
    """Extract a project name from a ``ProjectReference Include`` value.

    Accepts both forward- and backward-slash separators, strips the
    ``.csproj`` extension if present, and returns the bare filename
    stem. An empty or extension-less include path returns ``""``.
    """
    if not include:
        return ""
    normalised = include.replace("\\", "/")
    candidate = normalised.rsplit("/", 1)[-1]
    if candidate.lower().endswith(".csproj"):
        candidate = candidate[: -len(".csproj")]
    return candidate.strip()


def _load_inherited_properties(
    project_file: Path,
    root: Path,
) -> dict[str, str]:
    """Walk up to *root* collecting ``Directory.Build.{props,targets}``.

    MSBuild evaluates Directory.Build files from the deepest matching
    directory up to the solution root; properties from deeper files
    override shallower ones (closer-to-project wins). We mirror that
    ordering: start at *root* and merge downward, so each step
    overrides what came before, ending at the project's own directory.
    """
    inherited: dict[str, str] = {}
    chain = _directory_chain(project_file.parent, root)
    for directory in chain:
        for filename in _DIRECTORY_BUILD_FILES:
            candidate = directory / filename
            if not candidate.is_file():
                continue
            props_root = _parse_xml(candidate)
            if props_root is None:
                continue
            for elem in props_root.iter():
                local = _local_name(elem.tag)
                if local not in _INHERITED_PROPERTIES:
                    continue
                if elem.text and elem.text.strip():
                    inherited[local] = elem.text.strip()
    return inherited


def _directory_chain(start: Path, root: Path) -> list[Path]:
    """Return *root*..*start* as a list, root first.

    When *start* is not inside *root* (a defensive edge case for
    fixtures or tests that point outside the discovery tree) we
    return just ``[start]`` so the project's own directory is still
    inspected.
    """
    try:
        start.resolve().relative_to(root.resolve())
    except ValueError:
        return [start]
    chain: list[Path] = []
    current = start
    while True:
        chain.append(current)
        if current.resolve() == root.resolve():
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
    chain.reverse()
    return chain


def _parse_xml(path: Path) -> ET.Element | None:
    """Return the parsed XML root for *path* or ``None`` on any failure.

    csproj files are user-edited XML; tolerating bad input means a
    single typo does not kill discovery for the whole repo.
    """
    try:
        return ET.parse(path).getroot()
    except (ET.ParseError, OSError, UnicodeDecodeError):
        return None


def _local_name(tag: object) -> str:
    """Strip an XML namespace from *tag* and return the local name.

    MSBuild emits the ``http://schemas.microsoft.com/developer/msbuild/2003``
    namespace by default in older project formats; stripping it
    centrally lets the rest of the parser stay namespace-agnostic.
    """
    if not isinstance(tag, str):
        return ""
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


__all__ = ["extract"]
