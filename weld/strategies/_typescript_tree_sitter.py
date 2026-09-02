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
from weld.strategies._ts_call_sites import (
    bind_hint_target,
    read_ts_import_hint,
    strip_quotes as _strip_quotes,
)
from weld.strategies._ts_first_party import (
    FirstPartyImports,
    build_first_party_imports,
)
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


def build_caches(root: Path, language: str) -> dict[str, object] | None:
    """Pre-compute the per-root manifest / workspace / ``tsconfig`` caches.

    Returns ``None`` for non-TS / JS languages so the caller's dispatch
    stays a single ``is None`` check; otherwise returns a dict with
    ``package_deps``, ``node_modules_packages`` and ``first_party``
    keys. The helper is total: missing manifests / directories yield
    empty frozen-sets and an empty first-party index, not exceptions.

    ``first_party`` is the ADR 0142 D3 index -- the workspace member map
    plus the per-directory ``tsconfig`` alias memo. It is built here, once
    per discovery run, for the same reason the two manifest sets are: the
    files it reads are per-root, and paying for them per *source file*
    would be a manifest read for every import in the repository.
    """
    if language not in _TS_LANGUAGES:
        return None
    return {
        "package_deps": load_package_deps(root),
        "node_modules_packages": load_node_modules_packages(root),
        "first_party": build_first_party_imports(root),
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
    first_party: FirstPartyImports | None = None,
) -> None:
    """Emit per-import ``package`` nodes for the TS / JS file.

    The shared ``tree_sitter`` strategy already stamps the file-type
    node's ``imports_from``; this helper adds the relational layer
    (``package`` + ``depends_on``) so consumers can filter or rank
    imports by origin.

    ``package_deps``, ``node_modules_packages`` and ``first_party`` are
    optional caches so a strategy that processes many files in one root
    can pay the manifest read cost once. When omitted the helper
    computes them eagerly from ``root``; the manifest readers are total
    (they never raise) so an empty-cache result simply means the
    relevant signal was absent on disk.

    ADR 0142 D3: a specifier the *first-party* index binds -- an npm
    workspace member name, or a ``tsconfig`` alias in scope for this
    file -- names a file in this repository, not a package. It is
    recorded on the file node as ``props.import_targets`` and mints no
    package node at all, which is the treatment relative imports have
    always had. ``weld.graph_closure._link_imports`` reads that map and
    lands the ``depends_on`` edge on the defining file.
    """
    imports = list(symbols.get("imports", []))
    if not imports:
        return

    if package_deps is None:
        package_deps = load_package_deps(root)
    if node_modules_packages is None:
        node_modules_packages = load_node_modules_packages(root)
    if first_party is None:
        first_party = build_first_party_imports(root)

    # The file this helper is enriching, as the graph spells it. Read from
    # the props the caller is about to mint the node with, rather than taken
    # as a parameter, so the path the alias lookup is scoped by and the path
    # the node reports are the same string by construction.
    rel_path = str(node_props.get("file") or "")

    targets: dict[str, str] = {}
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

        bound = first_party.resolve(specifier, rel_path)
        if bound and bound != rel_path:
            targets[specifier] = bound
            continue

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

    if targets:
        # Sorted so the prop is byte-identical whatever order the grammar
        # captured the imports in -- the determinism contract every discover
        # path shares (ADR 0012).
        node_props["import_targets"] = dict(sorted(targets.items()))


def bind_call_imports(
    edges: list[dict],
    rel_path: str,
    first_party: FirstPartyImports | None,
) -> None:
    """Bind each call edge's import hint to the file its specifier names.

    The call-graph pass records *which* import introduced a callee's name
    (:mod:`weld.strategies._ts_call_sites`) but not what that name resolves
    to: the first-party index reads manifests off disk and only this layer
    holds it. So the two halves meet here, once per file, right after the
    edges are minted -- and the hint is complete before it ever reaches the
    graph, which is what lets ``weld._graph_closure_ts_calls`` stay a pure
    function of the nodes and edges it is handed.

    A specifier the index does not bind keeps ``target: ""``: a third-party
    package, or a relative import, which the closure resolves for itself
    against the path index rather than paying a filesystem walk for a
    question the graph can already answer.
    """
    if first_party is None:
        return
    for edge in edges:
        props = edge.get("props")
        hint = read_ts_import_hint(props)
        if hint is None:
            continue
        bound = first_party.resolve(hint.specifier, rel_path)
        if bound and bound != rel_path:
            bind_hint_target(props, bound)


__all__ = [
    "bind_call_imports",
    "build_caches",
    "enrich_file_node",
]
