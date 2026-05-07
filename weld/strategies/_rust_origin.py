"""Rust origin classification helpers (ADR 0042 § Rust).

Pure helpers used by the universal ``tree_sitter`` strategy and the
call-graph dispatcher in :mod:`weld.strategies._language_origin` to
stamp ``props.origin`` on every Rust-language node.

Per ADR 0042's Rust rule extended to cover ``Cargo.toml``-driven
classification:

* **stdlib** -- the use-path's first segment is one of the canonical
  Rust standard-library prefixes: ``std``, ``core``, ``alloc``, or
  ``proc_macro``. Bare identifiers like ``Vec`` or ``println`` do not
  qualify because they could be project-local without explicit
  ``use std::*`` resolution.
* **external** -- the use-path's first segment matches a crate
  declared in ``Cargo.toml``'s ``[dependencies]``,
  ``[dev-dependencies]``, ``[build-dependencies]``,
  ``[workspace.dependencies]``, or any of the target-conditional
  variants (``[target.<spec>.dependencies]`` and its
  ``dev-dependencies`` / ``build-dependencies`` sub-tables, where
  ``<spec>`` is either a ``cfg(...)`` predicate or an explicit target
  triple). The classifier unions every target spec unconditionally;
  it does not evaluate the predicate, because a crate that is only
  pulled in for one platform must still classify ``external`` rather
  than ``unresolved`` when read on another.
* **project** -- the use-path's first segment is the package name
  declared in ``Cargo.toml``'s ``[package].name``, or one of the Rust
  module-system path keywords (``crate``, ``self``, ``super``).
* **unresolved** -- empty use-path, or a path whose first segment
  cannot be matched against any of the above signals (e.g. a use-path
  rooted at a re-exported macro from an unresolved upstream).

Cargo allows a dependency declaration to override the imported crate
name via ``package = "real-name"``. The classifier therefore stores
the import-side name (the table key) rather than the underlying
package name -- ``use base64 as b64`` resolves against the table key,
not the on-disk crate. Cargo also normalises crate names by replacing
``-`` with ``_`` for Rust-side imports (a crate ``my-crate`` is
imported as ``my_crate``); the classifier accepts both shapes.

The helpers are deterministic and pure: they read only their
arguments, do not touch the filesystem, do not log, and import only
the standard library (``tomllib`` plus typing). The fixture-based
acceptance test for the four-way classification lives in
:mod:`weld.tests.weld_rust_origin_test` (see ``RustOriginFixtureTest``).
"""

from __future__ import annotations

import tomllib
from typing import Literal

#: ADR-0042 origin literal repeated locally so the strategies package
#: does not import :mod:`weld._graph_origin` (which lives in the
#: ``runtime`` target and would introduce a Bazel dep cycle).
Origin = Literal["project", "stdlib", "external", "unresolved"]

#: Canonical Rust standard-library / runtime crate prefixes.
#:
#: * ``std`` -- the platform-aware standard library.
#: * ``core`` -- the freestanding subset of ``std`` (no allocator).
#: * ``alloc`` -- heap-allocating types (``Vec``, ``Box``, ``String``)
#:   in ``no_std`` builds.
#: * ``proc_macro`` -- compiler-provided proc-macro support crate.
#:
#: Stored as a frozenset for fast membership tests; the underscore
#: variant is included so callers do not have to remember to feed both
#: shapes.
RUST_STDLIB_CRATES: frozenset[str] = frozenset(
    {"std", "core", "alloc", "proc_macro"}
)

#: Path keywords that always resolve inside the current crate. ``crate``
#: anchors at the crate root; ``self`` / ``super`` walk the module
#: hierarchy. Any of these classify as ``project`` regardless of the
#: package name.
RUST_PROJECT_PATH_KEYWORDS: frozenset[str] = frozenset(
    {"crate", "self", "super"}
)

#: Cargo dependency table keys consulted by the classifier. The order
#: is irrelevant because we union the values; we list them explicitly
#: rather than walking ``Cargo.toml`` reflectively so that a typo in a
#: future Cargo extension does not silently leak as ``project``.
_CARGO_DEPENDENCY_TABLES: tuple[str, ...] = (
    "dependencies",
    "dev-dependencies",
    "build-dependencies",
)


def _normalise_crate_name(name: str) -> str:
    """Return the Rust-side import shape for a Cargo crate name.

    Cargo accepts hyphens in the package name on disk (``serde-json``)
    but Rust source code refers to the crate via the underscore form
    (``serde_json``). The classifier stores the underscore shape so
    membership checks against captured use-paths work without further
    normalisation at the call site.
    """
    return name.replace("-", "_") if name else name


def parse_cargo_package_name(text: str) -> str:
    """Return the ``[package].name`` value from *text*, or ``""``.

    Accepts the full ``Cargo.toml`` content. Returns the empty string
    when the file is missing the ``[package]`` table, when the table
    has no ``name`` field, or when the document is not valid TOML. The
    returned name is the Rust-import shape (hyphens replaced with
    underscores) so callers can match it directly against captured
    use-paths.
    """
    if not text:
        return ""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return ""
    if not isinstance(data, dict):
        return ""
    package = data.get("package")
    if not isinstance(package, dict):
        return ""
    name = package.get("name")
    if not isinstance(name, str):
        return ""
    return _normalise_crate_name(name.strip())


def _collect_dependency_keys(section: object, sink: set[str]) -> None:
    """Add every Rust-import-shaped key from *section* into *sink*.

    *section* should be the value of a Cargo dependency-table-like map
    (``[dependencies]``, ``[dev-dependencies]``, ``[build-dependencies]``,
    or any of the target-conditional sub-tables). Non-dict values and
    non-string keys are skipped silently so a malformed manifest still
    produces a usable set rather than raising.
    """
    if not isinstance(section, dict):
        return
    for crate in section:
        if isinstance(crate, str) and crate:
            sink.add(_normalise_crate_name(crate))


def parse_cargo_dependencies(text: str) -> frozenset[str]:
    """Return the set of crate names declared as dependencies in *text*.

    Walks ``[dependencies]``, ``[dev-dependencies]``,
    ``[build-dependencies]``, ``[workspace.dependencies]`` (the
    workspace-root variant), and every ``[target.<spec>.dependencies]``
    block (plus its ``dev-dependencies`` and ``build-dependencies``
    sub-tables) found under ``[target]``. ``<spec>`` may be either a
    ``cfg(...)`` predicate or an explicit target triple; the classifier
    unions all of them unconditionally so a crate declared only for one
    platform still classifies ``external`` rather than ``unresolved``
    when read on another.

    The returned names are normalised to the Rust-import shape
    (hyphens replaced with underscores). The rename pattern
    ``foo = { package = "bar" }`` is handled naturally because the
    table key (``foo``) is the import-side name. When the document is
    malformed or contains none of these tables, the helper returns an
    empty frozenset rather than raising.
    """
    if not text:
        return frozenset()
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return frozenset()
    if not isinstance(data, dict):
        return frozenset()

    crates: set[str] = set()
    for key in _CARGO_DEPENDENCY_TABLES:
        _collect_dependency_keys(data.get(key), crates)

    workspace = data.get("workspace")
    if isinstance(workspace, dict):
        _collect_dependency_keys(workspace.get("dependencies"), crates)

    target = data.get("target")
    if isinstance(target, dict):
        for spec in target.values():
            if not isinstance(spec, dict):
                continue
            for sub_key in _CARGO_DEPENDENCY_TABLES:
                _collect_dependency_keys(spec.get(sub_key), crates)

    return frozenset(crates)


def _first_segment(use_path: str) -> str:
    """Return the leading path segment of a Rust use-path, or ``""``.

    Tree-sitter's Rust ``imports`` query captures either an identifier
    (single segment) or the leading ``path:`` of a ``scoped_identifier``
    (multi-segment). The captured token may already be a single segment
    (``std``) or a dotted path (``std::collections::HashMap``). Rust
    also accepts a leading ``::`` to anchor at the crate root; we strip
    that before extracting the first segment.
    """
    if not use_path:
        return ""
    cleaned = use_path.strip()
    if cleaned.startswith("::"):
        cleaned = cleaned[2:]
    if not cleaned:
        return ""
    head = cleaned.split("::", 1)[0]
    return head.strip()


def classify_rust_use_path(
    use_path: str,
    *,
    package_name: str,
    dependencies: frozenset[str],
) -> Origin:
    """Return the ADR 0042 origin for a Rust use-path.

    Args:
        use_path: The use-declaration argument as captured by
            tree-sitter (``"std::collections"``, ``"serde"``,
            ``"crate::utils"``, ``"my_crate::module"``). The leading
            ``::`` is tolerated.
        package_name: The current crate name from ``[package].name``
            in ``Cargo.toml``. Pass ``""`` when no manifest is
            available; the classifier still resolves stdlib and
            ``crate``/``self``/``super`` keywords.
        dependencies: The frozenset returned by
            :func:`parse_cargo_dependencies`. Pass an empty frozenset
            when no manifest is available.

    Returns:
        One of ``"stdlib"`` / ``"project"`` / ``"external"`` /
        ``"unresolved"``. The function is total: malformed inputs
        always yield ``"unresolved"``.
    """
    head = _first_segment(use_path)
    if not head:
        return "unresolved"

    if head in RUST_STDLIB_CRATES:
        return "stdlib"
    if head in RUST_PROJECT_PATH_KEYWORDS:
        return "project"

    head_norm = _normalise_crate_name(head)
    if package_name and head_norm == _normalise_crate_name(package_name):
        return "project"

    if head_norm in dependencies:
        return "external"

    return "unresolved"


__all__ = [
    "Origin",
    "RUST_PROJECT_PATH_KEYWORDS",
    "RUST_STDLIB_CRATES",
    "classify_rust_use_path",
    "parse_cargo_dependencies",
    "parse_cargo_package_name",
]
