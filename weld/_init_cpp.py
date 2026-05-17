"""C++ build-system detection and source-entry generation for ``wd init``.

Lives alongside :mod:`weld.init` so the ``init.py`` line-count cap does
not grow just to wire the C++ build-system globs (``CMakeLists.txt``,
``BUILD`` / ``BUILD.bazel``, ``meson.build``).

Without this wiring, ``wd init`` on a C++ repository emits only the
tree-sitter source globs (``**/*.cpp`` etc.) plus a root-level
``Makefile`` entry. A nlohmann/json-style header-only library produces
zero CMakeLists graph nodes, the public bench's ``njson-xrepo-01``
cross-repo question scores F1=0.00 even when extraction is otherwise
correct (cpp-extraction-quality investigation), and ``wd query
CMakeLists`` returns no results because no node exists -- only the
file-name index in :mod:`weld.file_index` knows about them.

Three pieces:

* :func:`detect_cpp_buildsystem` -- scans the file list once and
  returns a flag dict.
* :func:`cpp_buildsystem_source_entries` -- returns ready-to-use YAML
  source blocks that wire :mod:`weld.strategies.cpp_buildsystem_detector`
  against recursive build-system globs and
  :mod:`weld.strategies.config_file` against canonical root-level
  config singletons (``CMakeLists.txt``, ``.clang-format``,
  ``.clang-tidy``, ``WORKSPACE``, ``WORKSPACE.bazel``).
* :func:`wire_cpp_buildsystem_into_buckets` -- single-call wrapper
  invoked from :mod:`weld.init` so the orchestration code does not have
  to thread a separate ``cpp_buildsystem_flags`` parameter through the
  YAML generator.

Strategy choice
---------------

``cpp_buildsystem_detector`` is the only bundled strategy that
recognises every C++ build-system root file (CMakeLists.txt, Makefile,
GNUmakefile, meson.build, BUILD, BUILD.bazel) AND consumes a ``glob``
pattern. ``config_file`` only mints nodes from explicit ``files:``
lists, so a recursive ``glob: '**/CMakeLists.txt'`` paired with
``config_file`` would silently emit nothing. Pairing the recursive
glob with ``cpp_buildsystem_detector`` instead means every nested
CMakeLists also produces a ``package:cpp:<dir>`` and
``build-target:cmake:<project>:<stem>`` node. The dedicated parser
(:mod:`weld.strategies.cpp_cmake`) is independent: users wire it
themselves when they need the call-graph layer.

A second ``files: ['CMakeLists.txt']`` entry routed through
``config_file`` is emitted at root level. That guarantees a
``config:CMakeLists_txt`` node exists regardless of which strategies
are enabled later, so ``wd query CMakeLists`` always returns the
canonical root build script.
"""

from __future__ import annotations

from pathlib import Path

# Build-system root file names recognised by
# :mod:`weld.strategies.cpp_buildsystem_detector`. Kept as private
# module-level constants so the detector can be extended (e.g. to
# Conan / vcpkg / xmake roots) without touching the entry generator.
_CMAKE_NAMES: frozenset[str] = frozenset({"CMakeLists.txt"})
_BAZEL_NAMES: frozenset[str] = frozenset({"BUILD", "BUILD.bazel"})
_MESON_NAMES: frozenset[str] = frozenset({"meson.build"})

# Root-only canonical-singleton config files paired with the C++ stack.
# Each maps a basename -> the flag key set when it appears at the
# *repository root only* (nested copies do not fire). These are
# routed through :mod:`weld.strategies.config_file` rather than
# ``cpp_buildsystem_detector`` because they are configuration files
# (formatter / linter / Bazel workspace), not build-system roots that
# emit ``build-target`` nodes.
#
# Order is significant -- it drives the documented YAML emission order
# in :func:`cpp_buildsystem_source_entries` after the build-system
# globs.
_ROOT_CONFIG_FILES: tuple[tuple[str, str], ...] = (
    (".clang-format", "has_clang_format"),
    (".clang-tidy", "has_clang_tidy"),
    ("WORKSPACE", "has_workspace"),
    ("WORKSPACE.bazel", "has_workspace_bazel"),
)


def detect_cpp_buildsystem(
    files: list[Path], root: Path | None = None,
) -> dict[str, bool]:
    """Scan ``files`` and return flags driving C++ build-system wiring.

    Flags:

    * ``has_cmake`` -- at least one ``CMakeLists.txt`` is present
      anywhere in the tree. Drives the recursive glob entry that wires
      :mod:`weld.strategies.cpp_buildsystem_detector`.
    * ``has_root_cmake`` -- a ``CMakeLists.txt`` exists at the
      repository root. Drives the second ``files:`` singleton entry
      routed through :mod:`weld.strategies.config_file` so a config
      node always exists for the canonical root build script. Set only
      when ``root`` is provided; otherwise stays False.
    * ``has_bazel`` -- at least one ``BUILD`` or ``BUILD.bazel`` is
      present. Drives both Bazel globs.
    * ``has_meson`` -- at least one ``meson.build`` is present. Drives
      the meson glob entry.
    * ``has_clang_format`` / ``has_clang_tidy`` -- a ``.clang-format``
      or ``.clang-tidy`` exists at the *repository root*. Drives a
      ``files:`` singleton entry routed through ``config_file``.
      Nested-only copies do not fire (these are canonical-singleton
      tooling configs, not recursive globs).
    * ``has_workspace`` / ``has_workspace_bazel`` -- a ``WORKSPACE``
      or ``WORKSPACE.bazel`` exists at the repository root. Drives a
      ``files:`` singleton entry routed through ``config_file``.
      Note: ``MODULE.bazel`` is already wired by
      :data:`weld.init_detect_constants.ROOT_CONFIG_NAMES`, so it is
      not duplicated here.

    The scan is name-only (no file-content reads). ``root`` is taken
    explicitly rather than inferred from the file list because
    inferring a common-ancestor parent breaks down when only one file
    is in scope (the parent is then equal to the file's directory,
    which would falsely flag a nested-only ``CMakeLists.txt`` as a
    root-level one).
    """
    flags = {
        "has_cmake": False,
        "has_root_cmake": False,
        "has_bazel": False,
        "has_meson": False,
    }
    for _basename, flag_key in _ROOT_CONFIG_FILES:
        flags[flag_key] = False
    resolved_root = root.resolve() if root is not None else None
    # Map root-only basenames -> flag keys for O(1) lookup in the loop.
    root_config_lookup: dict[str, str] = {
        basename: flag_key for basename, flag_key in _ROOT_CONFIG_FILES
    }
    for f in files:
        name = f.name
        if name in _CMAKE_NAMES:
            flags["has_cmake"] = True
            if resolved_root is not None and not flags["has_root_cmake"]:
                try:
                    if f.resolve().parent == resolved_root:
                        flags["has_root_cmake"] = True
                except OSError:
                    pass
        elif name in _BAZEL_NAMES:
            flags["has_bazel"] = True
        elif name in _MESON_NAMES:
            flags["has_meson"] = True
        elif name in root_config_lookup and resolved_root is not None:
            flag_key = root_config_lookup[name]
            if flags[flag_key]:
                continue
            try:
                if f.resolve().parent == resolved_root:
                    flags[flag_key] = True
            except OSError:
                pass
    return flags


def _entry_glob(
    glob: str, node_type: str, strategy: str, *, comment: str,
) -> str:
    """Return a YAML source-entry block (glob form)."""
    lines: list[str] = [f"\n  # --- {comment} ---"]
    lines.append(f'  - glob: "{glob}"')
    lines.append(f"    type: {node_type}")
    lines.append(f"    strategy: {strategy}")
    return "\n".join(lines)


def _entry_files(
    file_list: list[str], node_type: str, strategy: str, *, comment: str,
) -> str:
    """Return a YAML source-entry block (files form)."""
    lines: list[str] = [f"\n  # --- {comment} ---"]
    inner = ", ".join(f'"{f}"' for f in file_list)
    lines.append(f"  - files: [{inner}]")
    lines.append(f"    type: {node_type}")
    lines.append(f"    strategy: {strategy}")
    return "\n".join(lines)


# Per-flag config-file singleton entry comments. Kept beside
# :data:`_ROOT_CONFIG_FILES` so adding a new root-config takes one
# new tuple here and one in the constant above -- no entry-generator
# edit needed.
_ROOT_CONFIG_COMMENTS: dict[str, str] = {
    "has_clang_format": "C++ formatter config (.clang-format singleton)",
    "has_clang_tidy": "C++ linter config (.clang-tidy singleton)",
    "has_workspace": "Bazel workspace root (WORKSPACE singleton)",
    "has_workspace_bazel": (
        "Bazel workspace root (WORKSPACE.bazel singleton)"
    ),
}


def cpp_buildsystem_source_entries(flags: dict[str, bool]) -> list[str]:
    """Return YAML source entries wiring the C++ build-system stack.

    Order, per the docstring contract on :mod:`weld._init_cpp`:

      1. ``glob: '**/CMakeLists.txt'`` -> ``cpp_buildsystem_detector``
         when ``has_cmake``.
      2. ``files: ['CMakeLists.txt']`` -> ``config_file`` when
         ``has_root_cmake`` (which implies ``has_cmake``).
      3. ``glob: '**/BUILD.bazel'`` -> ``cpp_buildsystem_detector``
         when ``has_bazel``.
      4. ``glob: '**/BUILD'`` -> ``cpp_buildsystem_detector`` when
         ``has_bazel``. Two entries are emitted (rather than one
         brace-expanded glob) because :mod:`weld.glob_match` does not
         expand brace patterns and the bench corpus includes both
         conventions.
      5. ``glob: '**/meson.build'`` -> ``cpp_buildsystem_detector``
         when ``has_meson``.
      6. ``files: ['.clang-format']`` -> ``config_file`` when
         ``has_clang_format``.
      7. ``files: ['.clang-tidy']`` -> ``config_file`` when
         ``has_clang_tidy``.
      8. ``files: ['WORKSPACE']`` -> ``config_file`` when
         ``has_workspace``.
      9. ``files: ['WORKSPACE.bazel']`` -> ``config_file`` when
         ``has_workspace_bazel``.

    Entries 6-9 are the root-only canonical-singleton configuration
    files (formatter, linter, Bazel workspace). They route through
    ``config_file`` (not
    ``cpp_buildsystem_detector``) because they are configuration
    files, not build-system roots that warrant a ``build-target``
    node. They emit independently of the build-system flags so a
    header-only repo that ships only ``.clang-format`` for downstream
    consumers still gets a node minted for it.

    ``has_root_cmake`` without ``has_cmake`` is treated as a no-op:
    the latter is a pre-condition of the former by construction (any
    root CMakeLists is also a CMakeLists), so a caller that sets the
    sub-flag in isolation has misused the detector and we refuse to
    emit a half-wired stack that would surprise them.
    """
    entries: list[str] = []

    if flags.get("has_cmake"):
        entries.append(_entry_glob(
            "**/CMakeLists.txt", "build-target",
            "cpp_buildsystem_detector",
            comment="C++ CMake build files (recursive)",
        ))
        if flags.get("has_root_cmake"):
            entries.append(_entry_files(
                ["CMakeLists.txt"], "config", "config_file",
                comment="C++ root CMakeLists.txt (canonical singleton)",
            ))

    if flags.get("has_bazel"):
        entries.append(_entry_glob(
            "**/BUILD.bazel", "build-target",
            "cpp_buildsystem_detector",
            comment="Bazel build files (BUILD.bazel)",
        ))
        entries.append(_entry_glob(
            "**/BUILD", "build-target",
            "cpp_buildsystem_detector",
            comment="Bazel build files (bare BUILD)",
        ))

    if flags.get("has_meson"):
        entries.append(_entry_glob(
            "**/meson.build", "build-target",
            "cpp_buildsystem_detector",
            comment="Meson build files",
        ))

    # Root-only config-file singletons. Emitted in the order documented
    # above and declared in :data:`_ROOT_CONFIG_FILES` so a single
    # source of truth governs both detector keys and YAML order.
    for basename, flag_key in _ROOT_CONFIG_FILES:
        if flags.get(flag_key):
            entries.append(_entry_files(
                [basename], "config", "config_file",
                comment=_ROOT_CONFIG_COMMENTS[flag_key],
            ))

    return entries


def wire_cpp_buildsystem_into_buckets(
    buckets: dict[str, list[str]],
    languages: dict[str, int],
    files: list[Path],
    root: Path,
) -> None:
    """Detect + emit C++ build-system entries directly into ``buckets``.

    Single-call wrapper invoked from :mod:`weld.init` so the
    orchestration code does not have to thread a separate
    ``cpp_buildsystem_flags`` parameter through ``generate_yaml``. No-ops
    when C++ is not detected; otherwise extends ``buckets['build']``
    with the entries returned by :func:`cpp_buildsystem_source_entries`.
    """
    if "cpp" not in languages:
        return
    flags = detect_cpp_buildsystem(files, root=root)
    buckets["build"].extend(cpp_buildsystem_source_entries(flags))
