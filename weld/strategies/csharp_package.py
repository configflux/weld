"""Strategy: ``package:csharp:<namespace>`` nodes for C# source files.

Closes the C# half of the gap that ``python_package`` fills for Python
(see ADR 0041 § Layer 3 ``file-anchor-symmetry``). After
``v0.19.1+f886ea6`` the shared tree-sitter strategy mints per-method
``symbol:cs:*`` nodes for every C# file in
``_TREE_SITTER_EMIT_CALLS``. Those file anchors gain outgoing
``contains`` edges and immediately trip Layer 3 unless an upstream
strategy emits ``package:csharp:* -> contains -> file:*``. This module
is that upstream strategy.

Design (per ADR 0060):

- Walk the configured ``glob`` for ``.cs`` files.
- Parse each file's *primary* namespace via
  :func:`weld.strategies._csharp_syntax.namespace_spans` (covers both
  file-scoped ``namespace Foo;`` and block-scoped
  ``namespace Foo { ... }``). The first declaration wins; multi-namespace
  files are vanishingly rare in real C# and the per-symbol nodes already
  capture per-class containers.
- Walk every ``.csproj`` and (after the source-derived pass) also
  mint a ``package:csharp:<root>`` node for each declared
  ``<RootNamespace>`` / ``<AssemblyName>`` / project-file-stem root that
  the source pass did not already cover. The csproj-derived root is
  anchored on every ``.cs`` file under the project directory whose
  source-declared namespace equals the root or is a descendant of it.
  This closes the federation gap (cross-repo, csharp, 2dh6) where a
  library's root namespace was only declared in nested-namespace files
  -- the sibling repo consumes ``using Newtonsoft.Json`` but no
  producer node carried ``props.name='Newtonsoft.Json'`` because no
  source file in the library declared the root in isolation. Mirrors
  :func:`weld.strategies._csharp_origin.load_project_namespace_roots_by_project`
  on the producer side so the ADR 0042 producer and consumer sides
  stay symmetric.
- Mint exactly one ``package:csharp:<dotted>`` node per discovered
  namespace (idempotent merge across files) and emit one ``contains``
  edge per file under that namespace.
- Files with no detectable namespace are skipped. The Layer 3
  entrypoint allow-list (Rule 2) covers the legitimate
  top-level-statement case.

Determinism (ADR 0012 § 3): file lists are sorted, the package node is
emitted exactly once per namespace, and the ``contains`` edges are
sorted by destination ID before emission so repeated runs produce
byte-identical graphs.

Origin (ADR 0042): every namespace minted by this strategy is
classified as ``project`` because the strategy only ever sees globs
inside the workspace -- the discovered namespaces are first-party by
construction. This mirrors how ``python_package`` classifies
project-rooted packages and matches the
``classify_using_import`` rule used by the C# tree-sitter import
handler when an import resolves against
``project_namespace_roots``.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from weld._node_ids import file_id as _canonical_file_id
from weld._node_ids import package_id as _canonical_package_id
from weld.strategies._csharp_origin import (
    load_project_namespace_roots_by_project,
)
from weld.strategies._csharp_syntax import namespace_spans
from weld.strategies._glob_resolve import resolve_glob
from weld.strategies._helpers import StrategyResult
from weld.strategies._provenance import file_provenance

_STRATEGY = "csharp_package"


def _read_text_safely(path: Path) -> str:
    """Return file text or empty string on read error.

    Mirrors ``weld._init_csharp._read_text_safely``. Discovery never
    crashes on unreadable bytes -- a binary blob masquerading as a
    ``.cs`` file should yield no signal rather than an exception.
    """
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _primary_namespace(source_text: str) -> str:
    """Return the first declared namespace in a C# file, or ``""``.

    File-scoped (``namespace Foo;``) and block-scoped
    (``namespace Foo { ... }``) declarations are both handled by
    :func:`namespace_spans`. The first match in source order is the
    primary namespace; multi-namespace files use the first one.
    """
    spans = namespace_spans(source_text)
    if not spans:
        return ""
    # spans is a list of (offset_after_decl, namespace_name); first
    # element is the textually-first declaration.
    return spans[0][1].strip()


def _is_descendant_namespace(ns: str, root_ns: str) -> bool:
    """Return True if ``ns`` equals ``root_ns`` or is a sub-namespace.

    Case-insensitive (C# namespaces are case-insensitive; ADR 0060). Empty
    inputs are not descendants of anything.
    """
    if not ns or not root_ns:
        return False
    n = ns.casefold()
    r = root_ns.casefold()
    return n == r or n.startswith(r + ".")


def _csproj_self_namespace_files(
    project_dir: Path,
    project_roots: frozenset[str],
    file_namespaces: dict[Path, str],
    root: Path,
) -> dict[str, list[Path]]:
    """Return per-root file lists for files under ``project_dir`` whose
    declared namespace matches each root or a descendant.

    The result is a dict keyed by root display-case namespace -> sorted
    list of ``.cs`` file paths. A root that anchors zero files (e.g. a
    csproj with no sources whose namespaces match the root) is omitted
    so the ADR 0060 invariant (every package has >=1 outgoing
    ``contains`` edge) holds.
    """
    out: dict[str, list[Path]] = {}
    try:
        project_dir.relative_to(root)
    except ValueError:
        return out
    for root_ns in project_roots:
        if not root_ns:
            continue
        matched: list[Path] = []
        for cs_path, declared_ns in file_namespaces.items():
            try:
                cs_path.relative_to(project_dir)
            except ValueError:
                continue
            if _is_descendant_namespace(declared_ns, root_ns):
                matched.append(cs_path)
        if matched:
            matched.sort()
            out[root_ns] = matched
    return out


def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Emit ``package:csharp:<namespace>`` nodes plus ``contains`` edges.

    Source schema:

    ``glob`` (required)
        Path glob for C# sources, e.g. ``"src/**/*.cs"`` or
        ``"**/*.cs"``. Same semantics as the C# tree-sitter source.
    ``exclude`` (optional)
        List of patterns passed to ``resolve_glob`` (same semantics as
        every other glob-driven strategy in the family).
    """
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source.get("glob", "")
    excludes = source.get("exclude") or []

    if not pattern:
        return StrategyResult(nodes, edges, discovered_from)

    matched: list[Path] = resolve_glob(root, pattern, excludes)
    if not matched:
        return StrategyResult(nodes, edges, discovered_from)

    # Group files by their declared primary namespace. Files with no
    # namespace are skipped from the source-derived pass (see module
    # docstring; Layer 3 entrypoint allow-list covers the legitimate
    # top-level-statement case). We also record per-file namespaces in a
    # flat map so the csproj branch below can anchor a root on every
    # file under the project whose declared namespace matches the root
    # or a descendant.
    by_namespace: dict[str, list[Path]] = defaultdict(list)
    file_namespaces: dict[Path, str] = {}
    for cs in sorted(matched):
        try:
            cs.relative_to(root)
        except ValueError:
            continue
        text = _read_text_safely(cs)
        if not text:
            continue
        ns = _primary_namespace(text)
        if not ns:
            continue
        by_namespace[ns].append(cs)
        file_namespaces[cs] = ns

    # csproj-derived self-namespace pass: for each project file walked by
    # ``_csharp_origin``, materialise a ``package:csharp:<root>`` node
    # per declared ``<RootNamespace>`` / ``<AssemblyName>`` /
    # project-file-stem (the same set the consumer-side classifier in
    # ``classify_using_import`` already uses). Anchor it on every ``.cs``
    # file under the project directory whose declared namespace matches
    # the root or is a descendant of it. This guarantees that a library
    # whose root namespace is only ever declared via nested-namespace
    # files (e.g. ``Newtonsoft.Json.Linq``, never bare
    # ``Newtonsoft.Json``) still emits a producer node carrying
    # ``props.name=<root>`` for cross-repo
    # :class:`weld.cross_repo.PackageImportResolver` matches.
    per_project = load_project_namespace_roots_by_project(root)
    for project_file in sorted(per_project.keys()):
        project_dir = project_file.parent
        project_roots = per_project[project_file]
        anchored = _csproj_self_namespace_files(
            project_dir, project_roots, file_namespaces, root
        )
        for root_ns, files in anchored.items():
            # Append in sorted order; the canonical id case-folds so
            # multiple display-case keys collapse to one pkg_nid below.
            for cs in files:
                if cs not in by_namespace.get(root_ns, ()):
                    by_namespace[root_ns].append(cs)

    if not by_namespace:
        return StrategyResult(nodes, edges, discovered_from)

    # Deduplicate ``(pkg_nid, file_nid)`` edges across multiple
    # display-case namespace keys that collapse to the same canonical
    # package id (e.g. ``Newtonsoft.Json`` declared by both a source
    # file's ``namespace`` and a csproj's ``<RootNamespace>NEWTONSOFT.JSON</RootNamespace>``).
    emitted_edges: set[tuple[str, str]] = set()

    for ns in sorted(by_namespace.keys()):
        files = by_namespace[ns]
        pkg_nid = _canonical_package_id("csharp", ns)
        # Idempotent merge: same strategy run may already have populated
        # this node from a different glob entry. Last-write wins on
        # props since both invocations carry the same values.
        nodes[pkg_nid] = {
            "type": "package",
            "label": ns,
            "props": {
                "name": ns,
                "language": "csharp",
                "source_strategy": _STRATEGY,
                "authority": "derived",
                "confidence": "definite",
                "roles": ["package"],
                # ADR 0042: only the workspace is walked here, so every
                # namespace minted by this strategy is first-party.
                "origin": "project",
            },
        }

        # Sort children by canonical file ID so the edge list is
        # byte-identical across runs.
        children: list[tuple[str, str]] = []
        for cs in files:
            rel_path = cs.relative_to(root).as_posix()
            file_nid = _canonical_file_id(rel_path)
            children.append((file_nid, rel_path))
        children.sort()
        # Per-file provenance (bd od2a). Unlike ``python_package``, the node
        # here is a *namespace* and carries no ``dir`` prop -- its members
        # can sit in any directory -- so the parent directories were never
        # the discovered thing, only a lossy stand-in for these files. That
        # is also why the local ``!= "./"`` filter this replaces was not a
        # fix: it suppressed the root marker by dropping the entry, leaving
        # a namespace declared at the repo root with no provenance at all.
        discovered_from.extend(file_provenance(root, files))

        for file_nid, _rel_path in children:
            key = (pkg_nid, file_nid)
            if key in emitted_edges:
                continue
            emitted_edges.add(key)
            edges.append(
                {
                    "from": pkg_nid,
                    "to": file_nid,
                    "type": "contains",
                    "props": {
                        "source_strategy": _STRATEGY,
                        "confidence": "definite",
                    },
                }
            )

    return StrategyResult(nodes, edges, sorted(set(discovered_from)))
