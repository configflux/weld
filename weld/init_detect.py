#!/usr/bin/env python3
"""Project structure detection for ``wd init``.

Scans a project directory to detect languages, frameworks, directory
structure, Dockerfiles, CI configs, Claude definitions, and documentation.
"""

from __future__ import annotations

from pathlib import Path

from weld._init_classify import Classification, classify_files
from weld._init_framework_scan import (
    _MAX_FILES_PER_LANG,
    iter_framework_scan_targets,
)
from weld._init_go_imports import iter_go_import_lines
from weld.init_detect_constants import (
    DOC_DIR_NAMES,
    MONOREPO_TOP_DIRS,
    ROOT_CONFIG_NAMES as _ROOT_CONFIG_NAMES_TUPLE,
)
from weld.repo_boundary import iter_repo_files

__all__ = ["_MAX_FILES_PER_LANG"]  # re-exported for tests

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
    # Real Go projects import gin via the canonical module path. The
    # surrounding context is typically ``import "..."`` (single import)
    # or a parenthesised ``import (\n\t"..."\n)`` block; ``_line_has_import``
    # treats any pattern that starts with ``"`` as a substring match
    # against the stripped source line.
    ('"github.com/gin-gonic/gin"', "Gin", "go_module"),
]

# Bounded-scan helpers (per-language exit and sampling cap) live in
# weld/_init_framework_scan.py. See ADR 0027.

# Directories to skip during scanning
SKIP_DIRS: frozenset[str] = frozenset({
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "env", ".env", ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
    ".bazel-cache", "bazel-out", "bazel-bin", "bazel-testlogs",
    ".claude", ".worktrees",
})

# Well-known root config file names. Re-exported as a list for backward
# compatibility with callers that imported the historical mutable name.
ROOT_CONFIG_NAMES: list[str] = list(_ROOT_CONFIG_NAMES_TUPLE)


def _classify(root: Path, files: list[Path]) -> Classification:
    """Build a fresh classification for ``files``.

    Public detectors call this when they are invoked directly (e.g. from
    a unit test). The orchestrator in :mod:`weld.init` builds the
    classification once and passes it to each detector via the
    ``_from_classified`` helpers below, avoiding the redundant walk.
    """
    return classify_files(root, files)

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
    # Go quoted-path patterns (``"github.com/gin-gonic/gin"``) appear inside
    # ``import "..."`` or a parenthesised ``import (\n\t"..."\n)`` block,
    # so the line can legitimately start with a double quote.
    #
    # For .go files, ``detect_frameworks`` feeds this function via
    # :func:`weld._init_go_imports.iter_go_import_lines`, which already
    # strips ``/* ... */`` block comments, backtick raw-string literals,
    # and lines outside an ``import`` context. That pre-filter is what
    # rules out the false-positive cases (paths embedded in comments,
    # raw strings, or stray ``var x = "..."`` assignments). The
    # substring containment check below is intentional defense-in-depth:
    # callers that bypass the pre-filter (e.g. unit tests passing a
    # single line directly) still get a correct answer for the cases the
    # iterator would have admitted, and the cheap ``//`` / ``#`` guard
    # keeps single-line comments out either way.
    is_go_quoted = pattern.startswith('"') and pattern.endswith('"')
    if is_go_quoted:
        if stripped.startswith(("#", "//")):
            return False
        return pattern in stripped
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

    The scan is bounded by three early-exit rules to keep ``wd init`` fast on
    large monorepos (see ADR 0027):

    * **Per-file early exit**: once every framework relevant to a file's
      language has been detected, the file's remaining lines are skipped.
    * **Per-language early exit**: once every framework that can be detected
      from a language family has been seen at least once anywhere in the
      repo, further files of that language are not opened.
    * **Per-language sampling cap**: at most ``_MAX_FILES_PER_LANG`` files
      per language family are read. One positive hit per framework is
      sufficient, so sampling does not change the detected set on
      well-organised repos and dramatically bounds the worst case.
    """
    detected: dict[str, tuple[str, str]] = {}
    for f, relevant, outstanding in iter_framework_scan_targets(
        files, FRAMEWORK_PATTERNS,
    ):
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        # Per-file remaining frameworks; when empty we break out of the
        # line loop for this file.
        file_remaining = {fw for (_, fw, _) in relevant}
        # Go quoted-path matching needs scan context (block comments,
        # raw-string spans, import-block boundaries). For .go files we
        # iterate only the lines that fall inside import context; other
        # languages keep simple line-by-line iteration.
        if f.suffix.lower() == ".go":
            line_iter = iter_go_import_lines(text)
        else:
            line_iter = iter(text.splitlines())
        for source_line in line_iter:
            if not file_remaining:
                break
            for pattern, framework, strategy in relevant:
                if (
                    framework in file_remaining
                    and _line_has_import(source_line, pattern)
                ):
                    rel = str(f.relative_to(root))
                    detected[framework] = (strategy, rel)
                    file_remaining.discard(framework)
                    outstanding.discard(framework)
    return [(fw, strat, path) for fw, (strat, path) in detected.items()]

def detect_structure(root: Path, files: list[Path] | None = None) -> str:
    """Detect if this is a monorepo or single-service project."""
    if files is None:
        found = sum(1 for d in MONOREPO_TOP_DIRS if (root / d).is_dir())
    else:
        found = len(_classify(root, files).monorepo_tops_seen)
    return "monorepo" if found >= 2 else "single-service"


def _detect_structure_from_classified(c: Classification) -> str:
    return "monorepo" if len(c.monorepo_tops_seen) >= 2 else "single-service"


def detect_dockerfiles(root: Path, files: list[Path]) -> list[str]:
    """Find Dockerfile patterns in the project.

    Returns glob patterns when a directory contains multiple Dockerfiles,
    or individual file paths otherwise.
    """
    return _detect_dockerfiles_from_classified(_classify(root, files))


def _detect_dockerfiles_from_classified(c: Classification) -> list[str]:
    patterns: list[str] = []
    docker_paths = c.docker_dir_files
    if len(docker_paths) > 1:
        exts = {Path(p).suffix for p in docker_paths}
        if ".Dockerfile" in exts:
            patterns.append("docker/*.Dockerfile")
        else:
            patterns.extend(sorted(docker_paths))
    else:
        patterns.extend(sorted(docker_paths))
    if c.has_root_dockerfile:
        patterns.append("Dockerfile")
    return patterns


def detect_compose(root: Path, files: list[Path]) -> list[str]:
    """Find docker-compose files."""
    return list(_classify(root, files).compose_files)


def _detect_compose_from_classified(c: Classification) -> list[str]:
    return list(c.compose_files)


def detect_ci(root: Path, files: list[Path]) -> list[str]:
    """Find CI workflow files."""
    return list(_classify(root, files).ci_files)


def _detect_ci_from_classified(c: Classification) -> list[str]:
    return list(c.ci_files)


def detect_claude(root: Path, files: list[Path]) -> tuple[list[str], list[str]]:
    """Detect Claude agent and command definitions."""
    c = _classify(root, files)
    return list(c.claude_agents), list(c.claude_commands)


def _detect_claude_from_classified(
    c: Classification,
) -> tuple[list[str], list[str]]:
    return list(c.claude_agents), list(c.claude_commands)


def detect_docs(root: Path, files: list[Path]) -> list[str]:
    """Find documentation directories."""
    return _detect_docs_from_classified(_classify(root, files))


def _detect_docs_from_classified(c: Classification) -> list[str]:
    seen = c.doc_dirs_seen
    # Preserve documented ordering from DOC_DIR_NAMES.
    return [name for name in DOC_DIR_NAMES if name in seen]


def find_python_glob_roots(root: Path, files: list[Path]) -> list[str]:
    """Find directory patterns containing Python files for glob entries."""
    return _find_python_glob_roots_from_classified(_classify(root, files))


def _find_python_glob_roots_from_classified(c: Classification) -> list[str]:
    py_dirs = c.py_dirs
    top_groups: dict[str, set[str]] = {}
    for d in py_dirs:
        top = d.split("/", 1)[0]
        top_groups.setdefault(top, set()).add(d)

    patterns: list[str] = []
    if c.has_root_py:
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
    return _detect_root_configs_from_classified(_classify(root, files))


def _detect_root_configs_from_classified(c: Classification) -> list[str]:
    visible = c.root_config_names
    return [name for name in ROOT_CONFIG_NAMES if name in visible]


def detect_all_from_classified(
    c: Classification,
) -> dict[str, object]:
    """Run every path-shape detector against ``c`` in one pass.

    Returns a dict with the kwargs that ``weld.init.generate_yaml``
    expects for the path-shape detectors. Framework detection,
    language counting, and ROS2 detection are *not* covered here -- they
    have their own scan strategies that the orchestrator wires
    separately.
    """
    agents, commands = _detect_claude_from_classified(c)
    return {
        "structure": _detect_structure_from_classified(c),
        "dockerfiles": _detect_dockerfiles_from_classified(c),
        "compose_files": _detect_compose_from_classified(c),
        "ci_files": _detect_ci_from_classified(c),
        "claude_agents": agents,
        "claude_commands": commands,
        "doc_dirs": _detect_docs_from_classified(c),
        "python_globs": _find_python_glob_roots_from_classified(c),
        "root_configs": _detect_root_configs_from_classified(c),
    }

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
