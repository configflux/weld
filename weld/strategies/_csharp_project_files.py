"""C# project -> file ownership resolver (ADR 0056 Addendum, 2026-05-15).

Resolves which ``.cs`` files each ``.csproj`` claims as compiled source so
the discovery pass can emit ``csproj://<name> -> contains -> file:<path>``
edges. Lives in its own module so ``csharp_project.py`` stays under the
400-line repo cap; the strategy delegates to :func:`resolve_owned_files`
once per project.

The hybrid resolution strategy follows the ADR 0056 addendum:

1. **SDK-style projects** (root ``<Project Sdk="..." />`` or any
   ``<Import Sdk="..." />`` child) use MSBuild's implicit ``**/*.cs``
   glob under the project directory, excluding well-known output dirs
   (``bin/``, ``obj/``, ``.vs/``, ``packages/``). Explicit
   ``<Compile Remove="..."/>`` entries subtract from this set; explicit
   ``<Compile Include="..."/>`` entries union additional files (typically
   pointing outside the project tree).
2. **Non-SDK projects** use only the explicit ``<Compile Include="..."/>``
   entries. This is the legacy shape; modern projects almost never need
   it.

Nested-csproj ownership is resolved at the strategy layer (not here):
``csharp_project.extract`` collects per-project file sets, then strips any
file owned by a *deeper* csproj before emitting edges. The deepest csproj
wins, mirroring MSBuild's behaviour when projects are co-located.

All path globs are resolved with ``pathlib.Path.glob`` semantics. A leading
``..`` in an ``<Include>`` value is allowed and tolerated -- the path is
resolved relative to the project directory, and if it lands outside the
discovery root the resolver drops it so we never mint a broken edge.
"""

from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET

#: Directories implicitly excluded from the SDK-style ``**/*.cs`` glob.
#: Matches MSBuild's ``DefaultExcludesInProjectFolder`` evaluated against
#: a freshly-created SDK-style project. ``packages/`` is the legacy NuGet
#: packages folder; ``.vs/`` is Visual Studio's per-solution cache.
_IMPLICIT_EXCLUDED_DIRS: frozenset[str] = frozenset({
    "bin",
    "obj",
    ".vs",
    "packages",
})


def is_sdk_style(project_xml: ET.Element) -> bool:
    """Return ``True`` when *project_xml* is an SDK-style project.

    A project is SDK-style when either:

    - the root ``<Project>`` element carries an ``Sdk=`` attribute, or
    - any direct ``<Import>`` child carries an ``Sdk=`` attribute (the
      multi-line SDK-import shape).

    A defensive ``False`` is returned for legacy projects (no Sdk hint),
    which then fall through to the explicit-Include-only path.
    """
    if "Sdk" in project_xml.attrib:
        return True
    for child in project_xml:
        if _local_name(child.tag) == "Import" and child.attrib.get("Sdk"):
            return True
    return False


def resolve_owned_files(
    project_file: Path,
    project_xml: ET.Element,
    root: Path,
) -> list[Path]:
    """Return the sorted list of ``.cs`` files *project_file* claims.

    Paths are absolute and lie inside *root*. Files outside *root* are
    dropped so the caller never mints an edge whose ``to`` resolves
    outside the discovery tree. The output is sorted by POSIX path so
    consecutive runs are byte-identical (ADR 0064 criterion 4).

    Resolution order mirrors MSBuild: start from the implicit SDK glob
    (or an empty set for non-SDK projects), then walk every
    ``<Compile>`` directive in *document order* and apply Include
    (union) or Remove (subtract). The document-order walk handles the
    edge case where a project does ``<Compile Remove="**\\*.cs"/>``
    then re-adds specific files via ``<Compile Include="...">``.
    """
    project_dir = project_file.parent

    if is_sdk_style(project_xml):
        candidates = _implicit_glob(project_dir)
    else:
        candidates = set()

    # Walk Compile directives in document order so Include/Remove
    # sequencing matches MSBuild evaluation. ``ET.Element.iter()``
    # returns descendants depth-first in document order.
    for elem in project_xml.iter():
        if _local_name(elem.tag) != "Compile":
            continue
        include = elem.attrib.get("Include", "").strip()
        remove = elem.attrib.get("Remove", "").strip()
        if include:
            candidates.update(_resolve_pattern(project_dir, include))
        if remove:
            for path in _resolve_pattern(project_dir, remove):
                candidates.discard(path)

    # Containment + canonicalisation in one pass: drop anything outside
    # *root* before sorting so the result is stable across OSes.
    try:
        root_resolved = root.resolve()
    except OSError:
        root_resolved = root
    owned: list[Path] = []
    for path in candidates:
        if not _is_inside(path, root_resolved):
            continue
        if not path.is_file():
            continue
        owned.append(path)
    owned.sort(key=lambda p: p.resolve().as_posix())
    return owned


def _implicit_glob(project_dir: Path) -> set[Path]:
    """Return the implicit ``**/*.cs`` set for an SDK-style project.

    Walks the project tree once and skips any directory whose name is
    in :data:`_IMPLICIT_EXCLUDED_DIRS`. Using a manual walk (instead of
    ``project_dir.rglob('*.cs')``) lets us prune large ``obj/`` and
    ``bin/`` subtrees without enumerating them, which keeps discovery
    fast on real corpora.
    """
    found: set[Path] = set()
    if not project_dir.is_dir():
        return found
    stack: list[Path] = [project_dir]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name in _IMPLICIT_EXCLUDED_DIRS:
                    continue
                stack.append(entry)
                continue
            if entry.is_file() and entry.suffix.lower() == ".cs":
                found.add(entry)
    return found


def _resolve_pattern(project_dir: Path, pattern: str) -> set[Path]:
    """Expand *pattern* relative to *project_dir* into concrete file paths.

    Handles both MSBuild path conventions (backslash separators) and the
    POSIX form ``pathlib`` expects. Globs containing ``**`` use
    :meth:`Path.glob`; literal paths are stat-checked directly so a
    typo in an explicit Include does not match by accident.
    """
    if not pattern:
        return set()
    normalised = pattern.replace("\\", "/")
    if any(ch in normalised for ch in "*?["):
        # Glob: anchor to project_dir and let pathlib handle traversal.
        try:
            return {p for p in project_dir.glob(normalised) if p.is_file()}
        except (OSError, ValueError):
            return set()
    candidate = (project_dir / normalised).resolve()
    if candidate.is_file() and candidate.suffix.lower() == ".cs":
        return {candidate}
    return set()


def _is_inside(path: Path, root_resolved: Path) -> bool:
    """Return ``True`` when *path* (resolved) is *inside* *root_resolved*."""
    try:
        path.resolve().relative_to(root_resolved)
        return True
    except (OSError, ValueError):
        return False


def _local_name(tag: object) -> str:
    """Strip an XML namespace from *tag* and return the local name.

    Mirrors the helper in :mod:`weld.strategies.csharp_project`; duplicated
    here so this module has no circular import on its caller.
    """
    if not isinstance(tag, str):
        return ""
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


__all__ = [
    "is_sdk_style",
    "resolve_owned_files",
]
