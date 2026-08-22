"""Strategy: C# solution (.sln) file extraction (ADR 0056 Wave 1).

A Visual Studio solution file (.sln) is a flat, line-oriented manifest
that lists every project in the solution plus its build
configurations. We parse it for two graph-shaping facts:

1. Each contained project becomes a ``contains`` edge:
   ``solution://<sln-stem> -> contains -> csproj://<project>``.

2. Solution-wide build configurations (``Debug|Any CPU``, ``Release|Any
   CPU``, ...) become a sorted, deduplicated list on the solution
   node's ``configurations`` prop.

.sln files are not XML; they use Visual Studio's bespoke format. The
parser is therefore a small line-state machine rather than an XML
walker. Every emitted edge ships with ``confidence="definite"`` per
ADR 0050 -- the format is deterministic even though it is not XML.

Wave 1 stops here: cross-solution dependencies (``ProjectReference``)
are handled by :mod:`weld.strategies.csharp_project`. Wave 2 strategies
(`csharp_aspnet_routes`, `csharp_efcore`, `csharp_test_framework`) and
Wave 3 (`csharp_msbuild_targets`) are tracked separately and depend on
this strategy plus its sibling for their fixture wiring.
"""

from __future__ import annotations

import re
from pathlib import Path

from weld.strategies._glob_resolve import resolve_glob
from weld.strategies._helpers import StrategyResult

#: Visual Studio's ``Project(...)`` line declares each project in the
#: solution. The shape is:
#:
#:     Project("{<type-guid>}") = "<name>", "<relative-path>", "{<id-guid>}"
#:
#: The capture group pulls the relative path so we can derive the
#: target ``csproj://<name>`` ID via the path's stem -- consistent with
#: :func:`weld.strategies.csharp_project._csproj_id`.
_PROJECT_RE = re.compile(
    r'^Project\([^)]*\)\s*=\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"'
)

#: Build configurations are listed inside the
#: ``SolutionConfigurationPlatforms`` section as ``<name> = <name>``
#: lines. The right-hand side is the display value; the two sides
#: match in practice for every shipping VS template.
_CONFIG_RE = re.compile(r'^\s*([^=]+?)\s*=\s*\1\s*$')

#: Suffix used to detect csproj entries in the solution. .sln files
#: can also list .vbproj / .fsproj / solution folders (which use a
#: special GUID and no real path); restricting to ``.csproj`` matches
#: ADR 0056's C#-only scope.
_CSPROJ_SUFFIX = ".csproj"


def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract solution nodes and contains edges to project nodes."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source.get("glob", "**/*.sln")
    excludes = source.get("exclude", [])

    matched = resolve_glob(root, pattern, excludes)

    for sln_file in matched:
        if not sln_file.is_file():
            continue

        rel_path = sln_file.relative_to(root).as_posix()
        discovered_from.append(rel_path)

        sln_name = sln_file.stem
        sln_id = _solution_id(sln_name)
        try:
            text = sln_file.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue

        project_names, configurations = _parse_solution(text)

        nodes[sln_id] = {
            "type": "build-target",
            "label": sln_name,
            "props": {
                "file": rel_path,
                "solution_name": sln_name,
                "source_strategy": "csharp_solution",
                "authority": "canonical",
                "confidence": "definite",
                "roles": ["build"],
                "configurations": sorted(set(configurations)),
                "project_count": len(project_names),
            },
        }

        # One ``contains`` edge per declared project. Sorted for
        # deterministic emit order.
        for project_name in sorted(set(project_names)):
            edges.append({
                "from": sln_id,
                "to": _csproj_id(project_name),
                "type": "contains",
                "props": {
                    "source_strategy": "csharp_solution",
                    "confidence": "definite",
                },
            })

    return StrategyResult(nodes, edges, discovered_from)


def _solution_id(name: str) -> str:
    """Return the canonical ``solution://<name>`` ID.

    The original-case filename stem is used so the ID is stable
    against the human-visible solution name; csproj IDs follow the
    same convention.
    """
    return f"solution://{name}"


def _csproj_id(project_name: str) -> str:
    """Return the canonical ``csproj://<name>`` ID.

    Kept private here (rather than imported from
    :mod:`weld.strategies.csharp_project`) so the two strategies stay
    independent per the ADR 0024 convention: strategies do not import
    each other.
    """
    return f"csproj://{project_name}"


def _parse_solution(text: str) -> tuple[list[str], list[str]]:
    """Return (project_names, configurations) parsed from a .sln body.

    The parser walks the file line by line. ``Project(...)`` lines
    are matched directly; configurations are gathered from the
    ``SolutionConfigurationPlatforms`` global section, which is a
    bounded block between ``GlobalSection(...) = preSolution`` and
    ``EndGlobalSection``.
    """
    projects: list[str] = []
    configurations: list[str] = []
    in_config_section = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        project_match = _PROJECT_RE.match(line)
        if project_match:
            relative_path = project_match.group(2)
            project_name = _project_name_from_path(relative_path)
            if project_name:
                projects.append(project_name)
            continue

        if line.startswith("GlobalSection(SolutionConfigurationPlatforms)"):
            in_config_section = True
            continue
        if line == "EndGlobalSection":
            in_config_section = False
            continue
        if in_config_section:
            cfg_match = _CONFIG_RE.match(line)
            if cfg_match:
                configurations.append(cfg_match.group(1).strip())

    return projects, configurations


def _project_name_from_path(relative_path: str) -> str:
    """Extract a project filename stem from a solution-relative path.

    Solution folders (entries whose path equals the project name,
    e.g. ``solution items``) and non-csproj entries (.vbproj /
    .fsproj / .shproj) are filtered out by requiring the ``.csproj``
    suffix. The suffix check is case-insensitive because Windows
    casing is not preserved in cross-platform repos.
    """
    if not relative_path:
        return ""
    normalised = relative_path.replace("\\", "/")
    candidate = normalised.rsplit("/", 1)[-1]
    if not candidate.lower().endswith(_CSPROJ_SUFFIX):
        return ""
    return candidate[: -len(_CSPROJ_SUFFIX)].strip()


__all__ = ["extract"]
