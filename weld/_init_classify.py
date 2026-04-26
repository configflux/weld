"""Single-pass file classifier for ``wd init`` (ADR 0027).

Background
----------
The light-weight ``detect_*`` helpers in :mod:`weld.init_detect` each
iterate the file list independently and call ``Path.relative_to`` per
file. With seven such helpers, ``relative_to`` was being called ~12x per
file on a 100k-file synthetic repo and dominated CPU under cProfile
(~54%).

This module folds those scans into a single walk that computes the
``relative_to`` once per file, materialises the cheap-to-derive
attributes (parts tuple, lower-cased suffix, POSIX string), and runs
every per-file detector concern in the same pass. The ``detect_*``
helpers in :mod:`weld.init_detect` keep their public signatures: they
classify the file list themselves when called directly, and accept a
precomputed classification when ``weld.init`` orchestrates the full
init flow.

Performance scope
-----------------
This refactor targets only the *path-shape* detectors (structure,
dockerfiles, compose, ci, claude, docs, python globs, root configs).
``detect_frameworks`` is independent: it reads file *contents* and is
already bounded under :mod:`weld._init_framework_scan`. ``detect_ros2``
parses the small set of ``package.xml`` files only and is also untouched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from weld.init_detect_constants import (
    CI_WORKFLOW_DIR_PARTS,
    CLAUDE_AGENT_DIR_PARTS,
    CLAUDE_COMMAND_DIR_PARTS,
    DOC_DIR_NAMES,
    MONOREPO_TOP_DIRS,
    ROOT_CONFIG_NAMES,
    YAML_SUFFIXES,
)


@dataclass(frozen=True)
class ClassifiedFile:
    """A single file annotated with its precomputed relative-path shape.

    ``parts`` is the result of ``path.relative_to(root).parts`` and is
    the only relative-path computation done per file. ``suffix`` is
    pre-lowered. ``posix`` is ``relative_to(root).as_posix()``. ``name``
    is the basename. Detectors read from these fields and never touch
    ``Path.relative_to`` again.
    """

    path: Path
    parts: tuple[str, ...]
    suffix: str
    name: str
    posix: str


@dataclass
class Classification:
    """Aggregate output of one classifier pass over the file list.

    Each field is consumed by exactly one detector concern. Fields are
    populated lazily during the walk: a detector that wants more state
    (e.g. ``find_python_glob_roots``) reads the corresponding field
    instead of re-walking ``files``.
    """

    files: list[ClassifiedFile] = field(default_factory=list)
    # detect_structure: top-level dir names (only those in MONOREPO_TOP_DIRS)
    monorepo_tops_seen: set[str] = field(default_factory=set)
    # detect_dockerfiles: relative posix paths under docker/, plus root Dockerfile flag
    docker_dir_files: list[str] = field(default_factory=list)
    has_root_dockerfile: bool = False
    # detect_compose: relative file names of root-level docker-compose*
    compose_files: list[str] = field(default_factory=list)
    # detect_ci: workflow file names (.yml/.yaml under .github/workflows/)
    ci_files: list[str] = field(default_factory=list)
    # detect_claude: agent and command file names under .claude/
    claude_agents: list[str] = field(default_factory=list)
    claude_commands: list[str] = field(default_factory=list)
    # detect_docs: which doc directory names appeared at top-level
    doc_dirs_seen: set[str] = field(default_factory=set)
    # find_python_glob_roots: directory paths that contain *.py files
    py_dirs: set[str] = field(default_factory=set)
    has_root_py: bool = False
    # detect_root_configs: root-level file names (only those in ROOT_CONFIG_NAMES)
    root_config_names: set[str] = field(default_factory=set)


def classify_files(root: Path, files: list[Path]) -> Classification:
    """Build a :class:`Classification` from ``files`` in one pass.

    For each file in ``files`` we compute ``relative_to(root)`` once and
    feed the resulting parts tuple, suffix, name, and POSIX string into
    every per-file concern that ``wd init`` cares about. Detectors then
    read from the aggregate fields instead of walking ``files`` again.
    """
    out = Classification()
    files_out = out.files
    monorepo_tops = out.monorepo_tops_seen
    doc_dirs_seen = out.doc_dirs_seen
    py_dirs = out.py_dirs
    root_configs_set = out.root_config_names

    for path in files:
        rel = path.relative_to(root)
        parts = rel.parts
        suffix = path.suffix.lower()
        name = path.name
        posix = rel.as_posix()
        files_out.append(ClassifiedFile(
            path=path, parts=parts, suffix=suffix, name=name, posix=posix,
        ))

        if not parts:
            continue

        top = parts[0]
        depth = len(parts)

        # detect_structure: monorepo markers in top-level
        if top in MONOREPO_TOP_DIRS:
            monorepo_tops.add(top)

        # detect_docs
        if top in DOC_DIR_NAMES:
            doc_dirs_seen.add(top)

        # detect_root_configs and root-level shapes
        if depth == 1:
            if name in ROOT_CONFIG_NAMES:
                root_configs_set.add(name)
            if name == "Dockerfile":
                out.has_root_dockerfile = True
            if (
                name.startswith("docker-compose")
                and suffix in YAML_SUFFIXES
            ):
                out.compose_files.append(name)
            if suffix == ".py":
                out.has_root_py = True

        # detect_dockerfiles: anything under docker/ with .Dockerfile or
        # name == "Dockerfile". Note: Path.suffix preserves case, but we
        # already lower-cased it; check name extension explicitly to keep
        # ``.Dockerfile`` (capital D) recognised.
        if top == "docker" and (
            posix.endswith(".Dockerfile") or name == "Dockerfile"
        ):
            out.docker_dir_files.append(posix)

        # detect_ci: .github/workflows/*.yml|.yaml
        if (
            depth == 3
            and parts[:2] == CI_WORKFLOW_DIR_PARTS
            and suffix in YAML_SUFFIXES
        ):
            out.ci_files.append(name)

        # detect_claude: agents and commands
        if depth == 3 and suffix == ".md":
            head = parts[:2]
            if head == CLAUDE_AGENT_DIR_PARTS:
                out.claude_agents.append(name)
            elif head == CLAUDE_COMMAND_DIR_PARTS:
                out.claude_commands.append(name)

        # find_python_glob_roots: track directories holding python files
        if suffix == ".py" and depth > 1:
            # Directory is everything except the file basename.
            py_dirs.add("/".join(parts[:-1]))

    return out
