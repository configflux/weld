"""C# artifact detection and source-entry generation for ``wd init``.

Lives alongside :mod:`weld.init` so the ``init.py`` line-count cap does
not grow just to wire the ADR 0056 Wave 1-3 C# strategy stack
(``csharp_solution``, ``csharp_project``, ``csharp_msbuild_targets``,
``csharp_test_framework``, ``csharp_aspnet_routes``, ``csharp_efcore``).

Without this wiring, ``wd init`` on a real .NET repository emits only the
tree-sitter line for ``**/*.cs`` and the project / solution / route /
EF Core / MSBuild / test-framework strategies are unreachable through
the quickstart -- users would have to read the strategy source to know
they exist.

Two pieces:

* :func:`detect_csharp_artifacts` -- scans the file list once and
  returns a flag dict that ``init.py`` consults before emitting source
  entries.
* :func:`csharp_source_entries` -- returns ready-to-use YAML source
  blocks, one per strategy that should fire. Matches the helper shape
  used by :mod:`weld._init_ros2`.
"""

from __future__ import annotations

from pathlib import Path

# Heuristic substrings used to classify .csproj contents. Matching is
# case-sensitive because the .NET tooling itself is case-sensitive on
# package IDs even on Windows; the canonical strings we look for are
# stable across SDK versions.
_ASPNET_CSPROJ_MARKERS: tuple[str, ...] = (
    "Microsoft.AspNetCore",
    'Sdk="Microsoft.NET.Sdk.Web"',
)
_EFCORE_CSPROJ_MARKER: str = "Microsoft.EntityFrameworkCore"
_TEST_FRAMEWORK_MARKERS: tuple[str, ...] = (
    "xunit",
    "nunit",
    "MSTest",
)
_DIRECTORY_BUILD_NAMES: frozenset[str] = frozenset({
    "Directory.Build.props",
    "Directory.Build.targets",
})


def _read_text_safely(path: Path) -> str:
    """Return file text or empty string on read error.

    Discovery never crashes on unreadable bytes -- a binary blob masquerading
    as a .csproj should yield no signal rather than an exception.
    """
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def detect_csharp_artifacts(files: list[Path]) -> dict[str, bool]:
    """Scan ``files`` and return flags driving C# source-entry wiring.

    Flags:

    * ``has_sln`` -- at least one ``.sln`` file present. Drives the
      ``csharp_solution`` source entry.
    * ``has_csproj`` -- at least one ``.csproj`` file present. Drives
      the ``csharp_project`` and ``csharp_msbuild_targets`` entries.
    * ``has_directory_build`` -- a ``Directory.Build.props`` or
      ``Directory.Build.targets`` is present anywhere in the tree.
      The wiring uses it as a hint for ``csharp_msbuild_targets``'s
      glob coverage (the strategy also parses .props/.targets, not
      just .csproj).
    * ``has_aspnet`` -- any .csproj references ``Microsoft.AspNetCore``
      (covers ``Microsoft.AspNetCore.App`` framework reference plus
      OpenApi / Mvc / etc package references) **or** declares the
      ASP.NET Core Web SDK (``Sdk="Microsoft.NET.Sdk.Web"``) **or** a
      ``Controllers/`` directory exists somewhere in the tree.
    * ``has_efcore`` -- any .csproj references
      ``Microsoft.EntityFrameworkCore``.
    * ``has_test_project`` -- any .csproj references one of the three
      canonical .NET test frameworks: xunit, nunit, or MSTest.

    The scan is bounded: each .csproj is read at most once and the
    flag set short-circuits per-file via ``remaining`` so a large
    repo with one big .csproj is not re-read for every flag.
    """
    flags = {
        "has_sln": False,
        "has_csproj": False,
        "has_directory_build": False,
        "has_aspnet": False,
        "has_efcore": False,
        "has_test_project": False,
    }

    csproj_paths: list[Path] = []
    for f in files:
        name = f.name
        suffix = f.suffix.lower()
        if suffix == ".sln":
            flags["has_sln"] = True
        elif suffix == ".csproj":
            flags["has_csproj"] = True
            csproj_paths.append(f)
        if name in _DIRECTORY_BUILD_NAMES:
            flags["has_directory_build"] = True
        if not flags["has_aspnet"] and "Controllers" in f.parts:
            flags["has_aspnet"] = True

    # Content-based signals only need to read each .csproj once.
    for proj in csproj_paths:
        if flags["has_aspnet"] and flags["has_efcore"] and flags["has_test_project"]:
            break
        text = _read_text_safely(proj)
        if not text:
            continue
        if not flags["has_aspnet"] and any(
            marker in text for marker in _ASPNET_CSPROJ_MARKERS
        ):
            flags["has_aspnet"] = True
        if not flags["has_efcore"] and _EFCORE_CSPROJ_MARKER in text:
            flags["has_efcore"] = True
        if not flags["has_test_project"] and any(
            marker in text for marker in _TEST_FRAMEWORK_MARKERS
        ):
            flags["has_test_project"] = True

    return flags


def _entry(
    glob: str, node_type: str, strategy: str, *, comment: str,
) -> str:
    """Return one YAML source-entry block matching ``weld/init._source_entry``."""
    lines: list[str] = [f"\n  # --- {comment} ---"]
    lines.append(f'  - glob: "{glob}"')
    lines.append(f"    type: {node_type}")
    lines.append(f"    strategy: {strategy}")
    return "\n".join(lines)


def csharp_source_entries(flags: dict[str, bool]) -> list[str]:
    """Return YAML source entries wiring every C# strategy the flags fire.

    Order, per the task spec:

      1. ``csharp_solution``        when ``has_sln``
      2. ``csharp_project``         when ``has_csproj``
      3. ``csharp_msbuild_targets`` when ``has_csproj`` (also parses
         Directory.Build.props/.targets when ``has_directory_build``)
      4. ``csharp_test_framework``  when ``has_test_project`` (Wave 2
         test-framework attributes live in .cs files)
      5. ``csharp_aspnet_routes``   when ``has_aspnet`` (conditional)
      6. ``csharp_efcore``          when ``has_efcore`` (conditional)

    Strategies 1-3 are XML-parsed and always definite. Strategy 4 needs
    a hint that test projects exist so we do not wire it on a pure
    library .NET repo (its scan is bounded but irrelevant noise).
    Strategies 5-6 are framework-aware; gating keeps the YAML focused.
    """
    entries: list[str] = []

    if flags.get("has_sln"):
        entries.append(_entry(
            "**/*.sln", "build-target", "csharp_solution",
            comment="C# solution graph (.sln)",
        ))
    if flags.get("has_csproj"):
        entries.append(_entry(
            "**/*.csproj", "build-target", "csharp_project",
            comment="C# project graph (.csproj + Directory.Build.*)",
        ))
        entries.append(_entry(
            "**/*.csproj", "build-target", "csharp_msbuild_targets",
            comment="C# MSBuild targets and ordering",
        ))
    if flags.get("has_test_project"):
        entries.append(_entry(
            "**/*.cs", "test-suite", "csharp_test_framework",
            comment="C# test frameworks (xUnit / NUnit / MSTest)",
        ))
    if flags.get("has_aspnet"):
        entries.append(_entry(
            "**/*.cs", "route", "csharp_aspnet_routes",
            comment="ASP.NET Core controllers and routes",
        ))
    if flags.get("has_efcore"):
        entries.append(_entry(
            "**/*.cs", "entity", "csharp_efcore",
            comment="EF Core DbContext and entities",
        ))

    return entries
