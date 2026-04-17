#!/usr/bin/env python3
"""Project structure detection for ``wd init``.

Scans a project directory to detect languages, frameworks, directory
structure, Dockerfiles, CI configs, Claude definitions, and documentation.
"""

from __future__ import annotations

from pathlib import Path

from weld.repo_boundary import iter_repo_files

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Map file extension to language name
EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".java": "java",
    ".kt": "kotlin",
    ".cs": "csharp",
    ".php": "php",
    ".swift": "swift",
    # C and C++ are folded into a single ``cpp`` bucket because the
    # tree-sitter-cpp grammar parses both.
    ".c": "cpp",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".h": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".hxx": "cpp",
    ".ipp": "cpp",
    ".tpp": "cpp",
}

# Framework detection: (import_pattern, framework_name, strategy_name)
FRAMEWORK_PATTERNS: list[tuple[str, str, str]] = [
    ("from fastapi", "FastAPI", "fastapi"),
    ("import fastapi", "FastAPI", "fastapi"),
    ("from django", "Django", "python_module"),
    ("import django", "Django", "python_module"),
    ("from flask", "Flask", "python_module"),
    ("import flask", "Flask", "python_module"),
    ("from sqlalchemy", "SQLAlchemy", "sqlalchemy"),
    ("import sqlalchemy", "SQLAlchemy", "sqlalchemy"),
    ("from pydantic", "Pydantic", "pydantic"),
    ("import pydantic", "Pydantic", "pydantic"),
    ("from prisma", "Prisma", "python_module"),
    ("import express", "Express", "python_module"),
    ("require('express')", "Express", "python_module"),
    ('require("express")', "Express", "python_module"),
    ("from gin", "Gin", "python_module"),
    ("import gin", "Gin", "python_module"),
]

# Directories to skip during scanning
SKIP_DIRS: frozenset[str] = frozenset({
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "env", ".env", ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
    ".bazel-cache", "bazel-out", "bazel-bin", "bazel-testlogs",
    ".claude", ".worktrees",
})

# Well-known root config file names
ROOT_CONFIG_NAMES: list[str] = [
    "MODULE.bazel", ".bazelrc", "CLAUDE.md", "AGENTS.md",
    ".env.example", "pyproject.toml", "package.json",
    "Makefile", "Cargo.toml", "go.mod",
]

# ---------------------------------------------------------------------------
# Detection functions
# ---------------------------------------------------------------------------

def scan_files(root: Path) -> list[Path]:
    """Return repo-visible regular files for init detection."""
    return iter_repo_files(root, fallback_excluded_dir_names=SKIP_DIRS)

def detect_languages(files: list[Path]) -> dict[str, int]:
    """Count files by language based on extension."""
    counts: dict[str, int] = {}
    for f in files:
        lang = EXT_TO_LANG.get(f.suffix.lower())
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: -x[1]))

def _line_has_import(line: str, pattern: str) -> bool:
    """Check if a source line contains the import pattern.

    Only matches at the start of a stripped line to avoid false positives
    from string literals or comments that mention framework names.
    """
    stripped = line.strip()
    if stripped.startswith(("#", "//", '"', "'", "(", "[")):
        return False
    if pattern.startswith(("from ", "import ")):
        return stripped.startswith(pattern)
    if "require(" in pattern:
        return (
            pattern in stripped
            and ("=" in stripped or stripped.startswith("require("))
        )
    return False

def detect_frameworks(
    root: Path, files: list[Path],
) -> list[tuple[str, str, str]]:
    """Grep source files for known framework imports.

    Returns list of (framework_name, strategy_name, detected_in_path).
    """
    detected: dict[str, tuple[str, str]] = {}
    scannable_exts = {
        ".py", ".js", ".ts", ".tsx", ".jsx", ".go",
        ".c", ".cc", ".cpp", ".cxx",
        ".h", ".hpp", ".hh", ".hxx", ".ipp", ".tpp",
    }

    for f in files:
        if f.suffix.lower() not in scannable_exts:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for source_line in text.splitlines():
            for pattern, framework, strategy in FRAMEWORK_PATTERNS:
                if framework not in detected and _line_has_import(source_line, pattern):
                    rel = str(f.relative_to(root))
                    detected[framework] = (strategy, rel)

    return [(fw, strat, path) for fw, (strat, path) in detected.items()]

def detect_structure(root: Path, files: list[Path] | None = None) -> str:
    """Detect if this is a monorepo or single-service project."""
    monorepo_markers = {"services", "apps", "packages", "libs"}
    if files is None:
        found = sum(1 for d in monorepo_markers if (root / d).is_dir())
    else:
        found = len({
            f.relative_to(root).parts[0]
            for f in files
            if f.relative_to(root).parts
            and f.relative_to(root).parts[0] in monorepo_markers
        })
    return "monorepo" if found >= 2 else "single-service"

def detect_dockerfiles(root: Path, files: list[Path]) -> list[str]:
    """Find Dockerfile patterns in the project.

    Returns glob patterns when a directory contains multiple Dockerfiles,
    or individual file paths otherwise.
    """
    patterns: list[str] = []
    rel_files = [f.relative_to(root).as_posix() for f in files]
    dockerfiles = [
        path
        for path in rel_files
        if path.startswith("docker/")
        and (
            Path(path).suffix == ".Dockerfile"
            or Path(path).name == "Dockerfile"
        )
    ]
    if len(dockerfiles) > 1:
        exts = {Path(path).suffix for path in dockerfiles}
        if ".Dockerfile" in exts:
            patterns.append("docker/*.Dockerfile")
        else:
            patterns.extend(sorted(dockerfiles))
    else:
        patterns.extend(sorted(dockerfiles))
    if "Dockerfile" in rel_files:
        patterns.append("Dockerfile")
    return patterns

def detect_compose(root: Path, files: list[Path]) -> list[str]:
    """Find docker-compose files."""
    return [
        f.relative_to(root).name
        for f in files
        if len(f.relative_to(root).parts) == 1
        and f.name.startswith("docker-compose")
        and f.suffix in (".yml", ".yaml")
    ]

def detect_ci(root: Path, files: list[Path]) -> list[str]:
    """Find CI workflow files."""
    return [
        rel.name
        for rel in (f.relative_to(root) for f in files)
        if len(rel.parts) == 3
        and rel.parts[0] == ".github"
        and rel.parts[1] == "workflows"
        and rel.suffix in (".yml", ".yaml")
    ]

def detect_claude(root: Path, files: list[Path]) -> tuple[list[str], list[str]]:
    """Detect Claude agent and command definitions."""
    rel_files = [f.relative_to(root) for f in files]
    agents = [
        rel.name
        for rel in rel_files
        if len(rel.parts) == 3
        and rel.parts[0] == ".claude"
        and rel.parts[1] == "agents"
        and rel.suffix == ".md"
    ]
    commands = [
        rel.name
        for rel in rel_files
        if len(rel.parts) == 3
        and rel.parts[0] == ".claude"
        and rel.parts[1] == "commands"
        and rel.suffix == ".md"
    ]
    return agents, commands

def detect_docs(root: Path, files: list[Path]) -> list[str]:
    """Find documentation directories."""
    doc_dirs: list[str] = []
    for name in ["docs", "doc", "documentation"]:
        if any(
            rel.parts and rel.parts[0] == name
            for rel in (f.relative_to(root) for f in files)
        ):
            doc_dirs.append(name)
    return doc_dirs

def find_python_glob_roots(root: Path, files: list[Path]) -> list[str]:
    """Find directory patterns containing Python files for glob entries."""
    py_dirs: set[str] = set()
    has_root_py = False
    for f in files:
        if f.suffix == ".py":
            rel_dir = str(f.parent.relative_to(root))
            if rel_dir == ".":
                has_root_py = True
            else:
                py_dirs.add(rel_dir)

    top_groups: dict[str, set[str]] = {}
    for d in py_dirs:
        top = d.split("/")[0]
        top_groups.setdefault(top, set()).add(d)

    patterns: list[str] = []
    if has_root_py:
        patterns.append("*.py")
    for top, dirs in sorted(top_groups.items()):
        if len(dirs) > 3:
            patterns.append(f"{top}/**/*.py")
        else:
            for d in sorted(dirs):
                patterns.append(f"{d}/*.py")
    return patterns

def detect_root_configs(root: Path, files: list[Path]) -> list[str]:
    """Find well-known root configuration files."""
    visible_root_files = {
        f.relative_to(root).name
        for f in files
        if len(f.relative_to(root).parts) == 1
    }
    return [name for name in ROOT_CONFIG_NAMES if name in visible_root_files]

def detect_ros2(root: Path, files: list[Path]) -> list[str]:
    """Return relative directories of ROS2 packages found under ``root``.

    A ROS2 package is a directory containing a ``package.xml`` whose manifest
    declares any ``<buildtool_depend>`` starting with ``ament_`` (the ROS2
    build-tool prefix, e.g. ``ament_cmake`` or ``ament_python``). This
    intentionally excludes classic catkin / non-ROS2 manifests.

    Results are POSIX-style relative paths sorted for determinism. An empty
    list means the workspace is not a ROS2 workspace; callers use this to
    gate ``ros2_*`` source entries in ``wd init``.
    """
    pkg_roots: list[str] = []
    for f in files:
        if f.name != "package.xml":
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "<buildtool_depend>ament_" not in text:
            continue
        rel_dir = f.parent.relative_to(root).as_posix()
        if rel_dir and rel_dir != ".":
            pkg_roots.append(rel_dir)
    return sorted(set(pkg_roots))
