"""C++ origin classification for graph nodes (ADR 0042 §C++).

Pure helpers used by the layer-1 tree-sitter emission and the layer-2
include resolver to stamp ``props.origin`` on every emitted/rewritten
``symbol`` / ``file`` / ``module`` node.

Per ADR 0042's C++ rule:

  * Resolved include under ``/usr/include/c++/...`` /
    ``/usr/include/clang/...`` / a toolchain libc++ or libstdc++ root,
    OR a callee in the ``std::`` namespace -> ``stdlib``.
  * Resolved include under any other system root
    (e.g. ``/usr/include/boost/``, ``/usr/include/eigen3/``) or a
    vendored third-party tree -> ``external``.
  * Resolved repo-local include -> ``project``.
  * Layer-1 unresolved sentinel that survives -> ``unresolved``.

The functions in this module are deterministic: they read only their
arguments, do not touch the filesystem, and never log.
"""

from __future__ import annotations

from pathlib import Path

#: Prefix roots that mark a C++ stdlib header location. These are the
#: conventional locations of libstdc++ (``/usr/include/c++/<gccver>/...``),
#: libc++ (``/usr/include/c++/v1/...``, ``/usr/lib/llvm-*/include/c++/...``),
#: and clang builtins (``/usr/include/clang/...``,
#: ``/usr/lib/llvm-*/lib/clang/<ver>/include``). The match is a literal
#: posix-string ``startswith`` check; we deliberately do not call
#: ``stat``/``readlink`` here so the classifier remains pure.
STDLIB_INCLUDE_ROOTS: tuple[str, ...] = (
    "/usr/include/c++/",
    "/usr/lib/llvm-",  # toolchain libc++/clang headers under llvm-N/
    "/usr/lib/gcc/",  # toolchain libstdc++ under gcc-N/
    "/usr/include/clang/",
    "/usr/local/include/c++/",
    "/usr/local/include/clang/",
    "/opt/homebrew/include/c++/",  # macOS Homebrew clang
    "/Library/Developer/CommandLineTools/usr/include/c++/",  # Apple toolchain
)

#: Stdlib path markers used by :func:`is_stdlib_include_path` after the
#: literal-prefix tuple above misses. Layouts like Nix store, Conda envs,
#: ``/opt/<tool>/`` custom toolchains, and Bazel hermetic ``external/``
#: trees all carry a hash- or version-stamped parent segment that no
#: literal prefix can capture, but every libstdc++ / libc++ / clang
#: builtin install ultimately has the headers under ``/include/c++/`` or
#: ``/lib/clang/``. Matching on those markers, anchored to a recognised
#: parent prefix, keeps the classifier deterministic and pure (no fs
#: calls, no regex) while extending coverage to the toolchain layouts
#: enumerated in the ADR 0042 follow-up tracker. A runtime probe
#: (``cc -E -x c++ -v``) and a ``discover.yaml`` override are explicit
#: non-goals there.
_STDLIB_PATH_MARKERS: tuple[str, ...] = (
    "/include/c++/",
    "/lib/clang/",
)

#: Parent-prefix anchors that scope the marker check above to known
#: toolchain families. Each entry must appear *before* one of the
#: markers in the path for the classifier to accept it.
_STDLIB_PARENT_PREFIXES: tuple[str, ...] = (
    "/nix/store/",  # Nix / NixOS hashed store paths
    "/miniconda3/",  # Conda (miniconda) install root or env
    "/anaconda3/",  # Conda (anaconda) install root or env
    "/conda/envs/",  # generic conda env layout
    "/opt/",  # /opt/<tool>/ custom toolchains
    "/external/",  # Bazel hermetic external/<repo>/...
    "external/",  # Bazel hermetic external/<repo>/... (relative)
)


def _matches_exotic_stdlib_layout(header_path_str: str) -> bool:
    """Return True if *header_path_str* matches a non-literal stdlib layout.

    Helper for :func:`is_stdlib_include_path` that recognises Nix,
    Conda, ``/opt/<tool>/``, and Bazel hermetic ``external/`` layouts
    where the variable parent segment makes a literal-prefix match
    impossible. The path must contain one of ``_STDLIB_PARENT_PREFIXES``
    *and*, somewhere after that anchor, one of the
    ``_STDLIB_PATH_MARKERS``.
    """
    for parent in _STDLIB_PARENT_PREFIXES:
        anchor = header_path_str.find(parent)
        if anchor < 0:
            continue
        tail_start = anchor + len(parent)
        tail = header_path_str[tail_start:]
        for marker in _STDLIB_PATH_MARKERS:
            # Strip the marker's leading slash so the marker matches
            # against the tail that immediately follows the anchor.
            if marker.lstrip("/") in tail:
                return True
    return False


#: Parent roots for the Debian/Ubuntu/Fedora arch-specific libstdc++
#: split (``/usr/include/<triple>/c++/<version>/...``, bd d3et / bd
#: nts6). Deliberately duplicated from ``_SYSTEM_C_INCLUDE_ROOTS`` in
#: ``_cpp_system_include.py`` rather than imported: that module
#: imports ``STDLIB_INCLUDE_ROOTS`` *from* this one, so the reverse
#: import would cycle, and classification must stay a pure function
#: of the path string regardless of which resolver root actually
#: found the file (see module docstring).
_MULTIARCH_CXX_PARENT_ROOTS: tuple[str, ...] = (
    "/usr/include/",
    "/usr/local/include/",
)


def _matches_multiarch_cxx_layout(header_path_str: str) -> bool:
    """Return True for the Debian/Ubuntu/Fedora multiarch libstdc++ split.

    Recognises ``<root>/<triple>/c++/<version>/...`` -- e.g.
    ``/usr/include/x86_64-linux-gnu/c++/13/bits/c++config.h`` -- where
    the multiarch triple (``x86_64-linux-gnu``, ``aarch64-linux-gnu``,
    ...) has no common literal spelling across architectures, so
    ``STDLIB_INCLUDE_ROOTS`` cannot list it as a prefix (bd d3et).

    Unlike :func:`_matches_exotic_stdlib_layout`, this anchors with
    ``startswith`` rather than "found anywhere in the string": ``/usr/
    include/`` and ``/usr/local/include/`` are short, generic-looking
    prefixes a project could easily embed several levels deep in a
    vendored cross-compilation sysroot (e.g. ``/repo/third_party/
    rpi-sysroot/usr/include/arm-linux-gnueabihf/c++/12/vector``);
    matching that substring anywhere, the way the Nix/Conda/``/opt/``
    markers do, would misclassify a project's own vendored copy as
    stdlib. Requiring the root at position 0, plus an exact
    ``<one-segment>/c++/<version>/`` shape immediately after it (a
    numeric-leading version -- ``13``, or dotted like the ``13.2.0``
    already accepted elsewhere in this module's Nix/Conda fixtures --
    never a project-style name such as ``vNext``), keeps the match
    bound to a real system include root the same way the ADR 0042
    markers bound theirs.
    """
    for root in _MULTIARCH_CXX_PARENT_ROOTS:
        if not header_path_str.startswith(root):
            continue
        remainder = header_path_str[len(root):]
        parts = remainder.split("/")
        if len(parts) < 3:
            continue
        triple, cxx, version = parts[0], parts[1], parts[2]
        is_version = version[:1].isdigit() and all(
            c.isdigit() or c == "." for c in version
        )
        if triple and cxx == "c++" and is_version:
            return True
    return False


def is_stdlib_include_path(header_path_str: str) -> bool:
    """Return True if *header_path_str* lives under a known stdlib root.

    Operates on a posix-form string so callers can pass either a real
    path or a synthetic test fixture (e.g. ``/usr/include/c++/13/vector``).
    Recognises the literal ``STDLIB_INCLUDE_ROOTS`` prefixes, the
    exotic-layout families (Nix, Conda, ``/opt/<tool>/``, Bazel
    hermetic ``external/<repo>/``) handled by
    :func:`_matches_exotic_stdlib_layout`, and the Debian/Ubuntu/
    Fedora arch-specific multiarch split handled by
    :func:`_matches_multiarch_cxx_layout`.
    """
    if not header_path_str:
        return False
    if any(header_path_str.startswith(root) for root in STDLIB_INCLUDE_ROOTS):
        return True
    if _matches_exotic_stdlib_layout(header_path_str):
        return True
    return _matches_multiarch_cxx_layout(header_path_str)


def is_std_namespace_callee(callee: str) -> bool:
    """Return True if *callee* is in the C++ ``std::`` namespace.

    Recognises the qualified forms produced by tree-sitter's call query:
    ``std::max``, ``std::vector::push_back``, ``::std::string``. A bare
    name (``max``) is not enough; ADR 0042 requires the explicit
    ``std::`` qualification because unqualified names cannot be
    distinguished from project-local symbols at this layer.
    """
    if not callee:
        return False
    if callee.startswith("std::"):
        return True
    if callee.startswith("::std::"):
        return True
    return False


def classify_resolved_include(
    header_path: Path,
    root: Path,
) -> str:
    """Return the ADR-0042 origin for a layer-2 resolved include.

    Args:
        header_path: Absolute path of the resolved header (the result of
            ``resolve_cpp_include`` or the layer-2 file index lookup).
        root: Repository root, used to decide whether the header is
            in-project.

    Returns:
        One of ``"project"``, ``"stdlib"``, ``"external"``. The
        ``"unresolved"`` value is reserved for the no-resolution case
        and is not produced by this function.
    """
    try:
        resolved = header_path.resolve()
    except OSError:
        # If we cannot resolve the path on disk, fall back to the raw
        # string so the classifier is still total. This branch is only
        # reachable on permission/symlink errors; the caller still gets
        # a deterministic answer.
        resolved_str = str(header_path)
    else:
        resolved_str = resolved.as_posix()
    try:
        root_resolved_str = root.resolve().as_posix()
    except OSError:
        root_resolved_str = str(root)

    # Project: under the repo root.
    root_prefix = root_resolved_str.rstrip("/") + "/"
    if resolved_str == root_resolved_str or resolved_str.startswith(root_prefix):
        return "project"

    # Stdlib roots take precedence over the broader external roots so
    # ``/usr/include/c++/13/vector`` does not collapse to ``external``.
    if is_stdlib_include_path(resolved_str):
        return "stdlib"

    # Anything else outside the repo is external (third-party, vendored,
    # or system non-stdlib).
    return "external"


def classify_layer2_origin(
    callee: str,
    header_path: Path,
    root: Path,
) -> str:
    """Return the layer-2 origin for a (callee, resolved-header) pair.

    Stdlib detection is order-sensitive: the ``std::`` namespace check
    runs first so a callee like ``std::max`` resolved through a
    repo-local re-export header is still classified ``stdlib`` (the
    callee's namespace is the authoritative signal in ADR 0042's
    C++ rule). Otherwise the header path drives the classification.
    """
    if is_std_namespace_callee(callee):
        return "stdlib"
    return classify_resolved_include(header_path, root)


def upgrade_origin(prior: str | None, new: str) -> str:
    """Pick the higher-information origin between *prior* and *new*.

    ADR 0042 layer-2 may upgrade a sentinel ``unresolved`` to a real
    origin once it has more information. Conversely, a layer-2 rewrite
    must never silently downgrade a definite ``project`` / ``stdlib`` /
    ``external`` claim to ``unresolved``. The precedence is:

        unresolved < project | stdlib | external

    Among the three definite values, *new* wins (the layer-2 pass has
    strictly more context than layer-1). Unknown values fall through to
    *new* as well so the function stays total.
    """
    if prior is None:
        return new
    if prior == "unresolved":
        return new
    if new == "unresolved":
        return prior
    return new


__all__ = [
    "STDLIB_INCLUDE_ROOTS",
    "classify_layer2_origin",
    "classify_resolved_include",
    "is_std_namespace_callee",
    "is_stdlib_include_path",
    "upgrade_origin",
]
