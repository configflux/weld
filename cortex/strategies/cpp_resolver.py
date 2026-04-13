"""C++ cross-file include resolver (layer 2 of cortex-cpp-ros2).

Layer 1 (``cortex/strategies/tree_sitter.py``) emits
``symbol:unresolved:<name>`` sentinels for every C++ call because the
per-file tree-sitter pass has no cross-translation-unit context.

This module provides the layer-2 pass that:

  * Walks each ``.cpp/.cc`` file's ``#include "header"`` set.
  * Resolves each include relative to the file's directory and a small
    set of conventional search dirs (``include/``, ``src/``, ``inc/``).
  * Looks up which symbols those headers define (parsed by the same
    tree-sitter strategy via ``_parse_file_symbols``).
  * Rewrites matching ``symbol:unresolved:<callee>`` edges to
    ``symbol:cpp:<header_module>:<callee>`` and stamps them ``definite``.

Hard limits — these mirror python_callgraph's import-table pass and are
deliberate, not bugs:

  * Only ``#include "..."`` (string-literal) form is followed; system
    ``<...>`` includes are ignored.
  * Header search paths: file's parent dir, then a small fixed set of
    conventional dirs under root. We do NOT read CMake / Bazel for
    full include-path discovery.
  * Match strategies for qualified callees: exact match, then
    ``Class::method`` against (classes_in_header, exports_in_header),
    then tail-segment fallback. Overload sets collapse on name only.
  * Header/impl dedupe is preserved: layer 2 only adds new resolved
    nodes and never downgrades a layer-1 ``definite`` node.
"""

from __future__ import annotations

from pathlib import Path

from cortex.strategies._helpers import should_skip

CPP_HEADER_EXTS: frozenset[str] = frozenset(
    {".h", ".hh", ".hpp", ".hxx", ".ipp", ".tpp", ".inc"}
)

# Conventional header search dirs, tried in order, relative to root.
CPP_SEARCH_DIRS: tuple[str, ...] = (
    "",  # root itself
    "include",
    "src",
    "inc",
)

def _ts_module_from_path(rel_path: str) -> str:
    """Return a stable dotted module path matching tree_sitter helper.

    Duplicated from ``cortex.strategies.tree_sitter._ts_module_from_path``
    so this module has no import-time dependency on tree_sitter.
    """
    p = Path(rel_path)
    parts = list(p.parts)
    if not parts:
        return ""
    parts[-1] = p.stem
    return ".".join(parts)

def resolve_cpp_include(
    root: Path,
    file_path: Path,
    include_text: str,
) -> Path | None:
    """Resolve an ``#include "..."`` directive to a header path.

    Args:
        root: Repository root used as the search anchor.
        file_path: Absolute path of the source file containing the
            directive (the include is first resolved relative to its
            parent directory).
        include_text: Raw text captured by the tree-sitter ``imports``
            query, e.g. ``"foo.h"`` (with quotes), ``<iostream>`` for
            system includes (which return None), or a bare ``foo.h``.

    Returns:
        Absolute Path to the resolved header, or None if the include
        is a system include or no candidate file exists.
    """
    if not include_text:
        return None
    text = include_text.strip()
    # Reject system includes outright.
    if text.startswith("<") and text.endswith(">"):
        return None
    # Strip surrounding quotes from the captured ``"foo.h"`` form.
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1]
    if not text:
        return None

    root_resolved = root.resolve()

    # 1. Resolve relative to the including file's directory.
    candidate = (file_path.parent / text).resolve()
    try:
        candidate.relative_to(root_resolved)
        if candidate.is_file():
            return candidate
    except ValueError:
        # Outside the root — fall through to the search-dir loop.
        pass

    # 2. Walk the conventional search dirs under root.
    for search in CPP_SEARCH_DIRS:
        base = root_resolved / search if search else root_resolved
        candidate = (base / text).resolve()
        if candidate.is_file():
            try:
                candidate.relative_to(root_resolved)
                return candidate
            except ValueError:
                continue
    return None

def match_callee(
    callee: str,
    header_exports: set[str],
    header_classes: set[str],
) -> bool:
    """Return True if *callee* is plausibly defined by a header with
    the given exports/classes set.

    Match strategies (cheapest first):
      1. Exact: ``callee in header_exports``.
      2. Qualified ``Class::method``: class is declared in the header
         and the trailing ``method`` segment is in the export set.
      3. Tail-match fallback: the final ``::``-segment of the callee
         is in the export set.
    """
    if not callee:
        return False
    if callee in header_exports:
        return True
    if "::" in callee:
        cls, _, tail = callee.rpartition("::")
        if cls in header_classes and tail in header_exports:
            return True
        if tail in header_exports:
            return True
    return False

def augment_state_with_headers(
    root: Path,
    per_file: list[dict],
    language: str,
    excludes: list,
    parse_symbols,
) -> None:
    """Walk the repo for C++ headers and append parses to *per_file*.

    Headers are typically not in the configured ``**/*.cpp`` glob, so
    we discover them ourselves to populate the resolver index.  This
    does NOT emit graph nodes for headers; the resolver mints resolved
    symbol nodes lazily on a hit.

    Args:
        root: Repository root.
        per_file: Mutable list of per-file state dicts produced by the
            tree_sitter strategy. New header entries are appended.
        language: Always ``"cpp"`` here; passed through to *parse_symbols*.
        excludes: Source-entry exclude globs honoured by ``should_skip``.
        parse_symbols: Callable matching the
            ``_parse_file_symbols(file_path, language, queries)`` shape
            from the tree_sitter module. Bound by the caller so this
            module has no hard dep on tree_sitter at import time.
    """
    seen: set[Path] = set()
    for entry in per_file:
        try:
            seen.add(entry["abs_path"].resolve())
        except OSError:
            continue

    root_resolved = root.resolve()
    for ext in CPP_HEADER_EXTS:
        for hdr in sorted(root_resolved.rglob(f"*{ext}")):
            try:
                hdr_resolved = hdr.resolve()
            except OSError:
                continue
            if hdr_resolved in seen:
                continue
            if not hdr.is_file():
                continue
            if should_skip(hdr, excludes):
                continue
            try:
                hdr.relative_to(root_resolved)
            except ValueError:
                continue
            try:
                symbols = parse_symbols(hdr, language)
            except Exception:
                continue
            rel_path = str(hdr_resolved.relative_to(root_resolved))
            module_path = _ts_module_from_path(rel_path)
            per_file.append(
                {
                    "abs_path": hdr_resolved,
                    "rel_path": rel_path,
                    "module_path": module_path,
                    "imports": list(symbols.get("imports", [])),
                    "exports_set": set(symbols.get("exports", [])),
                    "classes_set": set(symbols.get("classes", [])),
                    "file_caller_id": (
                        f"symbol:{language}:{module_path}:<file>"
                    ),
                }
            )
            seen.add(hdr_resolved)

def resolve_includes_pass(
    root: Path,
    per_file: list[dict],
    nodes: dict[str, dict],
    edges: list[dict],
) -> None:
    """Rewrite unresolved C++ call edges across #include boundaries.

    Mutates *nodes* and *edges* in place. Runs after the file walk so
    every header has been parsed and contributed its exports.
    """
    if not per_file:
        return

    # Index every parsed file by absolute resolved path so includes can
    # find them. Headers and impls share this index.
    file_index: dict[Path, dict] = {}
    for entry in per_file:
        try:
            key = entry["abs_path"].resolve()
        except OSError:
            continue
        file_index[key] = entry

    # Build a per-edge rewrite plan: for each edge whose target is an
    # unresolved sentinel and whose origin file has resolvable includes
    # that define the callee, swap the target id and mark resolved.
    new_nodes: dict[str, dict] = {}
    rewrites_by_target: dict[str, int] = {}

    for entry in per_file:
        # Layer 2 only acts on impl files. Headers don't host call
        # sites in our schema (their bodies are inline functions whose
        # ``<file>`` caller already lives in the header's own module).
        if entry["abs_path"].suffix in CPP_HEADER_EXTS:
            continue

        includes_text = entry.get("imports") or []
        resolved_headers: list[Path] = []
        for inc in includes_text:
            hdr = resolve_cpp_include(root, entry["abs_path"], inc)
            if hdr is None:
                continue
            resolved_headers.append(hdr.resolve())

        # Look up parsed header entries (skip headers we did not parse).
        header_entries = [
            file_index[h] for h in resolved_headers if h in file_index
        ]
        if not header_entries:
            continue

        file_caller = entry["file_caller_id"]
        for edge in edges:
            if edge.get("from") != file_caller:
                continue
            target = edge.get("to", "")
            if not target.startswith("symbol:unresolved:"):
                continue
            callee = target[len("symbol:unresolved:"):]
            if not callee:
                continue

            # Try each included header until one claims the callee.
            for hdr in header_entries:
                if not match_callee(
                    callee, hdr["exports_set"], hdr["classes_set"]
                ):
                    continue
                hdr_module = hdr["module_path"]
                resolved_id = f"symbol:cpp:{hdr_module}:{callee}"
                if resolved_id not in nodes and resolved_id not in new_nodes:
                    new_nodes[resolved_id] = {
                        "type": "symbol",
                        "label": callee,
                        "props": {
                            "file": hdr["rel_path"],
                            "module": hdr_module,
                            "qualname": callee,
                            "language": "cpp",
                            "resolved": True,
                            "source_strategy": "tree_sitter",
                            "authority": "derived",
                            "confidence": "definite",
                            "roles": ["implementation"],
                        },
                    }
                edge["to"] = resolved_id
                edge["props"]["resolved"] = True
                edge["props"]["confidence"] = "definite"
                rewrites_by_target[target] = (
                    rewrites_by_target.get(target, 0) + 1
                )
                break

    # Merge any newly created resolved nodes (never overwriting an
    # existing higher-confidence node).
    for nid, node in new_nodes.items():
        nodes.setdefault(nid, node)

    # Drop unresolved sentinel nodes that no longer have any inbound
    # edges. (Other files may still need them.)
    if rewrites_by_target:
        still_used: set[str] = {
            e["to"]
            for e in edges
            if e.get("to", "").startswith("symbol:unresolved:")
        }
        for target in rewrites_by_target:
            if target not in still_used and target in nodes:
                del nodes[target]
