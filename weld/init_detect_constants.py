"""Shared constants for the ``wd init`` classifier and detectors.

Kept in a stand-alone module so :mod:`weld._init_classify` and
:mod:`weld.init_detect` can both import them without a cycle. None of
these names are part of the public API; their layout mirrors the
detector code so each detector's matching condition is expressed once.
"""

from __future__ import annotations

# detect_structure markers. A repo is monorepo when at least two of
# these appear as top-level directories (any file rooted in them is
# enough to register the directory as present).
MONOREPO_TOP_DIRS: frozenset[str] = frozenset(
    {"services", "apps", "packages", "libs"},
)

# Documentation directory names that detect_docs reports.
DOC_DIR_NAMES: tuple[str, ...] = ("docs", "doc", "documentation")

# CI workflow directory layout: ``.github/workflows/*.yml|.yaml``.
CI_WORKFLOW_DIR_PARTS: tuple[str, str] = (".github", "workflows")

# Claude agents and commands live under fixed third-level paths.
CLAUDE_AGENT_DIR_PARTS: tuple[str, str] = (".claude", "agents")
CLAUDE_COMMAND_DIR_PARTS: tuple[str, str] = (".claude", "commands")

# YAML suffixes accepted by detect_compose and detect_ci.
YAML_SUFFIXES: frozenset[str] = frozenset({".yml", ".yaml"})

# Well-known root configuration files. Order is preserved so
# detect_root_configs returns them in the documented order.
#
# The list carries one canonical manifest per ecosystem weld extracts from:
# ``pyproject.toml`` for Python, ``package.json`` for Node, ``Cargo.toml``
# for Rust, ``go.mod`` for Go, ``MODULE.bazel`` / ``.bazelrc`` for Bazel,
# ``Makefile`` for Make. ``tsconfig.json`` is TypeScript's, and it was the
# one missing: weld already *reads* it for ``compilerOptions.paths`` alias
# resolution (``weld.strategies._ts_tsconfig_paths``), yet ``wd init`` left
# it unclaimed, so the manifest that decides how a TS project resolves its
# own imports never became a node in that project's graph (bd 5038-q9hba).
ROOT_CONFIG_NAMES: tuple[str, ...] = (
    "MODULE.bazel", ".bazelrc", "CLAUDE.md", "AGENTS.md",
    ".env.example", "pyproject.toml", "package.json", "tsconfig.json",
    "Makefile", "Cargo.toml", "go.mod",
)
