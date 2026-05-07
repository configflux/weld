"""TypeScript / JavaScript enrichments for the shared tree-sitter strategy.

Mirrors :mod:`weld.strategies._java_tree_sitter` and
:mod:`weld.strategies._csharp_tree_sitter`: adds a ``package`` node and
a ``depends_on`` edge for every import the tree-sitter ``imports``
query captures, with ``props.origin`` classified per ADR 0042's TS / JS
rule (see :mod:`weld.strategies._typescript_origin`).

Origin resolution is performed once per discovery run: the helper is
called with the project ``root`` and pre-computes the
``package.json`` dependency set and the ``node_modules/`` top-level
directory listing, then classifies each import specifier against
those caches. The classifier itself is pure; only the cache build
touches the filesystem.
"""

from __future__ import annotations

from pathlib import Path

from weld._node_ids import package_id as _canonical_package_id
from weld.strategies._typescript_origin import (
    classify_import_specifier,
    load_node_modules_packages,
    load_package_deps,
    package_root_from_specifier,
)

#: Languages handled by this enricher. The shared ``tree_sitter``
#: strategy dispatches by ``language`` and routes ts/tsx/js/jsx files
#: through this module so the ADR 0042 import-origin classification
#: is consistent across the four variants.
_TS_LANGUAGES: frozenset[str] = frozenset(
    {"typescript", "tsx", "javascript", "jsx"}
)


def build_caches(root: Path, language: str) -> dict[str, frozenset[str]] | None:
    """Pre-compute the per-root ``package.json`` / ``node_modules/`` caches.

    Returns ``None`` for non-TS / JS languages so the caller's dispatch
    stays a single ``is None`` check; otherwise returns a dict with
    ``package_deps`` and ``node_modules_packages`` keys. The helper is
    total: missing manifests / directories yield empty frozen-sets,
    not exceptions.
    """
    if language not in _TS_LANGUAGES:
        return None
    return {
        "package_deps": load_package_deps(root),
        "node_modules_packages": load_node_modules_packages(root),
    }


def enrich_file_node(
    nodes: dict[str, dict],
    edges: list[dict],
    file_node_id: str,
    node_props: dict,
    symbols: dict[str, list[str]],
    source_text: str,
    source_strategy: str,
    *,
    root: Path,
    package_deps: frozenset[str] | None = None,
    node_modules_packages: frozenset[str] | None = None,
) -> None:
    """Emit per-import ``package`` nodes for the TS / JS file.

    The shared ``tree_sitter`` strategy already stamps the file-type
    node's ``imports_from``; this helper adds the relational layer
    (``package`` + ``depends_on``) so consumers can filter or rank
    imports by origin.

    ``package_deps`` and ``node_modules_packages`` are optional caches
    so a strategy that processes many files in one root can pay the
    manifest read cost once. When omitted the helper computes them
    eagerly from ``root``; the manifest readers are total (they never
    raise) so an empty-cache result simply means the relevant signal
    was absent on disk.
    """
    imports = list(symbols.get("imports", []))
    if not imports:
        return

    if package_deps is None:
        package_deps = load_package_deps(root)
    if node_modules_packages is None:
        node_modules_packages = load_node_modules_packages(root)

    seen: set[str] = set()
    for raw in imports:
        if not raw:
            continue
        # The tree-sitter ``imports`` query captures the source string
        # *literal* including its quote characters; strip them so the
        # specifier matches the keys in ``package.json`` and the
        # entries under ``node_modules/``.
        specifier = _strip_quotes(raw)
        if not specifier:
            continue
        if specifier in seen:
            continue
        seen.add(specifier)

        origin = classify_import_specifier(
            specifier,
            package_deps=package_deps,
            node_modules_packages=node_modules_packages,
        )
        if origin == "project":
            # Relative imports point at another project file, not a
            # package. The ``tree_sitter`` strategy already mints file
            # nodes for those via the project glob, and ADR 0042 does
            # not require synthesising a per-file ``package`` node for
            # relative imports. Skip so the graph stays uncluttered.
            continue

        package_name = package_root_from_specifier(specifier)
        if not package_name:
            continue
        package_node_id = _canonical_package_id("typescript", package_name)
        package_props: dict = {
            "name": package_name,
            "language": "typescript",
            "source_strategy": source_strategy,
            "authority": "derived",
            "confidence": "definite",
            # ADR 0042 §TS / JS: the origin tag is computed from the
            # specifier, the project's ``package.json`` deps, and the
            # contents of ``node_modules/``. ``classify_import_specifier``
            # is total so this is always one of the four origins.
            "origin": origin,
        }
        nodes.setdefault(
            package_node_id,
            {
                "type": "package",
                "label": package_name,
                "props": package_props,
            },
        )
        edges.append(
            {
                "from": file_node_id,
                "to": package_node_id,
                "type": "depends_on",
                "props": {
                    "import_name": specifier,
                    "source_strategy": source_strategy,
                    "confidence": "definite",
                },
            }
        )


def _strip_quotes(raw: str) -> str:
    """Strip the surrounding quote characters from a tree-sitter capture.

    The TypeScript grammar's ``import_statement`` source rule captures
    the string node *with* its delimiters (``"react"`` not ``react``).
    The strip is conservative: only the matching first/last character
    is removed, and only when both ends agree on a quote style.
    """
    if len(raw) < 2:
        return raw
    first = raw[0]
    last = raw[-1]
    if first == last and first in ('"', "'", "`"):
        return raw[1:-1]
    return raw


__all__ = [
    "build_caches",
    "enrich_file_node",
]
