"""Frameworks detected on-disk but not yet wired to a strategy (ADR 0043).

Extracted from :mod:`weld._capabilities_registry` to keep both files
under the 400-line cap. Imported back via
:data:`weld._capabilities_registry.MISSING_FRAMEWORK_PATTERNS`.

Invariant (enforced by
``test_missing_patterns_disjoint_from_known_frameworks``): no
framework name and no basename in this table may overlap with a
wired strategy in :data:`weld._capabilities_registry.STRATEGY_CAPABILITIES`.
Examples of already-owned basenames (and the strategy that owns them):
``Makefile`` / ``GNUmakefile`` -> ``manifest`` (npm+make);
``CMakeLists.txt`` -> ``ros2_cmake``; ``Chart.yaml`` and ``*.tf``
-> ``deploy_surface`` (k8s+helm+terraform). Re-introducing them
here would double-count the same files in ``--missing``.
"""

from __future__ import annotations

# name -> (extensions, basenames)
# ``dotnet`` was here pre-ADR-0056. Wave 1 wires csharp_project and
# csharp_solution to ``framework="dotnet"`` so .csproj / .sln are no
# longer missing. F# / VB.NET (.fsproj / .vbproj) remain unsupported
# but the disjoint invariant forbids overlap with the framework
# name; tracking F#/VB.NET separately is a future ADR.
MISSING_FRAMEWORK_PATTERNS: dict[
    str, tuple[tuple[str, ...], tuple[str, ...]]
] = {
    "maven": ((), ("pom.xml",)),
    "gradle": (
        (),
        (
            "build.gradle",
            "build.gradle.kts",
            "settings.gradle",
            "settings.gradle.kts",
        ),
    ),
    "cargo": ((), ("Cargo.toml",)),
    "go_modules": ((), ("go.mod",)),
    "swift_pm": ((), ("Package.swift",)),
    "composer": ((), ("composer.json",)),
    "bundler": ((), ("Gemfile",)),
    "pyproject": ((), ("pyproject.toml",)),
    "requirements_txt": ((), ("requirements.txt",)),
    "setuptools": ((), ("setup.py", "setup.cfg")),
    # ``helm`` and ``terraform`` are owned by ``deploy_surface``
    # (``Chart.yaml`` and ``*.tf`` respectively); see its
    # multi-framework declaration in
    # :data:`weld._capabilities_registry.STRATEGY_CAPABILITIES`
    # and the per-framework split in
    # :data:`weld._capabilities_registry.MULTI_FRAMEWORK_FILES`.
}
