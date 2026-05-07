"""TypeScript / JavaScript origin classification (ADR 0042 §TS / JS).

Extends the per-language origin contract to TS/JS by adding the two
signals that the bare ``_language_origin`` helpers cannot provide on
their own: ``package.json`` dependency manifests and on-disk
``node_modules/`` package directories. The bundled ``tree_sitter``
strategy uses these helpers to classify every import specifier (and
the resulting ``package`` node) as exactly one of the four ADR 0042
origins.

Per ADR 0042's TS / JS rule:

  * **stdlib** -- the JS/TS implicit-lookup globals (``Array``, ``Math``,
    ``console``, ``Object``, ``Promise``, ``JSON``, ...). The list is
    re-exported from :mod:`weld.strategies._language_origin` so callers
    have one canonical set. Per-import specifiers prefixed with
    ``node:`` (the explicit Node.js builtin protocol) also classify as
    stdlib; the bare unprefixed forms (``"fs"``, ``"path"``, ``"os"``,
    ...) are *not* automatically stdlib because npm publishes packages
    with the same names and only manifest resolution can disambiguate.
  * **external** -- import specifier that resolves under
    ``node_modules/`` (top-level package directory exists) **or** that
    matches a key in ``package.json``'s ``dependencies``,
    ``devDependencies``, ``peerDependencies``, or
    ``optionalDependencies``. Scoped packages (``@scope/pkg``) and
    sub-path imports (``lodash/fp``) classify on the package root
    (``@scope/pkg`` / ``lodash``).
  * **project** -- relative import specifiers (``./foo``, ``../bar``,
    ``./a/b/c``).
  * **unresolved** -- anything else (bare specifier with no manifest
    or ``node_modules`` evidence). The classifier never silently
    coerces an ambiguous specifier into one of the three definite
    values; ADR 0042 demands an explicit ``unresolved`` tag.

The helpers are pure with one explicit exception: the manifest /
filesystem readers (``load_package_deps``, ``load_node_modules_packages``)
do exactly one ``Path.is_dir`` / ``Path.read_text`` on the supplied
``root`` so callers can pre-compute the deps and pass them into the
classifier. ``classify_import_specifier`` itself is pure -- no I/O,
no logging.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from weld.strategies._language_origin import JS_BUILTIN_GLOBALS

#: ADR-0042 origin literal repeated locally so the strategies package
#: does not import :mod:`weld._graph_origin` (which lives in the
#: ``runtime`` target and would introduce a Bazel dep cycle). The two
#: literal definitions stay in lock-step by ADR review.
Origin = Literal["project", "stdlib", "external", "unresolved"]

#: Re-export of the JS/TS implicit-lookup globals frozen-set so callers
#: have one canonical name without reaching into the sibling module.
JS_STDLIB_GLOBALS: frozenset[str] = JS_BUILTIN_GLOBALS

#: ``package.json`` keys that declare a package as an external
#: dependency. ADR 0042 deliberately covers the four conventional
#: dependency buckets (npm / pnpm / yarn all support these); peer and
#: optional are included so a transitive dep declared at peer scope is
#: still classified ``external`` rather than dropping to ``unresolved``.
_PACKAGE_JSON_DEP_KEYS: tuple[str, ...] = (
    "dependencies",
    "devDependencies",
    "peerDependencies",
    "optionalDependencies",
)

#: Node.js builtin module specifiers carry the ``node:`` protocol prefix
#: under the modern resolver (``import {readFile} from "node:fs"``).
#: Specifiers using that prefix are unambiguous stdlib regardless of
#: ``package.json`` content.
_NODE_BUILTIN_PREFIX = "node:"


def is_relative_import(specifier: str) -> bool:
    """Return True if *specifier* is a relative-path import.

    Per the ECMAScript module resolver, only specifiers starting with
    ``./`` or ``../`` are relative. A bare ``foo``, an absolute
    ``/abs/path``, or a URL (``https://...``) is not relative; those go
    through the bare-specifier branch where ``package.json`` and
    ``node_modules`` decide the origin.
    """
    if not specifier:
        return False
    return specifier.startswith("./") or specifier.startswith("../")


def is_js_stdlib_specifier(specifier: str) -> bool:
    """Return True if *specifier* is unambiguously a JS / TS stdlib import.

    Recognises two forms:

      * The ``node:<name>`` protocol prefix (``"node:fs"``,
        ``"node:path"``, ``"node:stream/web"``). The prefix is the
        Node.js-sanctioned way to disambiguate a builtin from a
        user-installed package with the same name and is the only form
        we accept for the import path.
      * The bare implicit-lookup globals from ``JS_STDLIB_GLOBALS``
        (``"Array"``, ``"Math"``, ``"console"``). These almost never
        appear as *import* specifiers (you import ``React``, not
        ``Math``) but the helper accepts them for parity with the
        call-graph sentinel classifier so a strategy that funnels both
        kinds of names through one path stays consistent.
    """
    if not specifier:
        return False
    if specifier.startswith(_NODE_BUILTIN_PREFIX):
        return True
    return specifier in JS_STDLIB_GLOBALS


def package_root_from_specifier(specifier: str) -> str:
    """Return the package-root portion of an import specifier.

    Strips sub-paths (``"lodash/fp"`` -> ``"lodash"``) while preserving
    scoped-package roots (``"@scope/pkg/sub"`` -> ``"@scope/pkg"``).
    Specifiers without a ``/`` pass through unchanged. The function is
    total and never raises.
    """
    if not specifier:
        return ""
    if specifier.startswith("@"):
        # Scoped: keep the first two segments (``@scope/pkg``).
        parts = specifier.split("/", 2)
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
        return specifier
    # Bare: strip everything after the first ``/``.
    return specifier.split("/", 1)[0]


def load_package_deps(root: Path) -> frozenset[str]:
    """Return the set of declared deps from ``<root>/package.json``.

    Reads the four conventional dep buckets (``dependencies``,
    ``devDependencies``, ``peerDependencies``, ``optionalDependencies``)
    and returns the union of their keys. Returns an empty set when the
    manifest is missing, malformed JSON, or carries no recognised dep
    keys; the function never raises so a strategy can call it once per
    discovery run without try/except wrapping.
    """
    manifest = root / "package.json"
    if not manifest.is_file():
        return frozenset()
    try:
        text = manifest.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return frozenset()
    try:
        data: Any = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return frozenset()
    if not isinstance(data, dict):
        return frozenset()
    deps: set[str] = set()
    for key in _PACKAGE_JSON_DEP_KEYS:
        bucket = data.get(key)
        if isinstance(bucket, dict):
            for dep_name in bucket.keys():
                if isinstance(dep_name, str) and dep_name:
                    deps.add(dep_name)
    return frozenset(deps)


def load_node_modules_packages(root: Path) -> frozenset[str]:
    """Return the set of top-level package directories under ``node_modules/``.

    Includes both unscoped packages (``node_modules/lodash`` ->
    ``"lodash"``) and scoped packages (``node_modules/@scope/pkg`` ->
    ``"@scope/pkg"``). Hidden directories (those starting with ``.``)
    are skipped, as ``node_modules/.bin`` and similar are tooling
    artefacts rather than package roots. Returns an empty set when
    ``node_modules/`` is missing or unreadable; the function never
    raises.
    """
    node_modules = root / "node_modules"
    if not node_modules.is_dir():
        return frozenset()
    packages: set[str] = set()
    try:
        entries = list(node_modules.iterdir())
    except OSError:
        return frozenset()
    for entry in entries:
        name = entry.name
        if not name or name.startswith("."):
            continue
        try:
            is_dir = entry.is_dir()
        except OSError:
            continue
        if not is_dir:
            continue
        if name.startswith("@"):
            # Scoped: enumerate the inner directory layer.
            try:
                scoped_entries = list(entry.iterdir())
            except OSError:
                continue
            for sub in scoped_entries:
                sub_name = sub.name
                if not sub_name or sub_name.startswith("."):
                    continue
                try:
                    if not sub.is_dir():
                        continue
                except OSError:
                    continue
                packages.add(f"{name}/{sub_name}")
        else:
            packages.add(name)
    return frozenset(packages)


def classify_import_specifier(
    specifier: str,
    *,
    package_deps: frozenset[str],
    node_modules_packages: frozenset[str],
) -> Origin:
    """Return the ADR-0042 origin for a TS / JS import specifier.

    Resolution order:

      1. Empty specifier -> ``unresolved``.
      2. Relative (``./`` / ``../``) -> ``project``.
      3. Stdlib (``node:`` prefix, or a JS implicit global) -> ``stdlib``.
      4. Package root in ``package_deps`` or ``node_modules_packages``
         -> ``external``.
      5. Otherwise -> ``unresolved``.

    The function is pure and total: every input maps to exactly one
    of the four origins. Order matters: stdlib runs before the manifest
    check so a ``node:fs`` import is not misclassified as ``external``
    if a project happens to depend on a package called ``fs`` (which
    npm publishes as a real package).
    """
    if not specifier:
        return "unresolved"
    if is_relative_import(specifier):
        return "project"
    if is_js_stdlib_specifier(specifier):
        return "stdlib"
    package_root = package_root_from_specifier(specifier)
    if package_root and (
        package_root in package_deps or package_root in node_modules_packages
    ):
        return "external"
    return "unresolved"


__all__ = [
    "JS_STDLIB_GLOBALS",
    "classify_import_specifier",
    "is_js_stdlib_specifier",
    "is_relative_import",
    "load_node_modules_packages",
    "load_package_deps",
    "package_root_from_specifier",
]
