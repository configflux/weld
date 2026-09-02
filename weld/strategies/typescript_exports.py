"""Strategy: Exported symbols from TypeScript / TSX modules.

Uses tree-sitter for AST-based extraction when available, falling back to
regex-based extraction otherwise.

AST path (tree-sitter available):
- Parses TypeScript files using tree-sitter and the query definitions from
  ``weld/languages/typescript.yaml``
- Produces richer nodes: exports list, types (classes/interfaces), imports
- Emits ``confidence: "definite"`` for AST-confirmed exports

Regex fallback path (tree-sitter unavailable):
- Uses line-level regex to find ``export function|class|const|...``
- Cannot capture re-exports, barrel files, or dynamic exports
- Emits ``confidence: "inferred"``

See: weld/docs/adr/0002-tree-sitter-optional-dependency.md

"""

from __future__ import annotations

import re
from pathlib import Path

from weld._rel_path import rel_to_root
from weld.strategies import _ts_dialect
from weld.strategies._glob_resolve import resolve_glob_with_provenance
from weld.strategies._helpers import StrategyResult
from weld.strategies._tree_sitter_ids import (
    canonical_file_node_id as _make_node_id,
    legacy_file_node_id as _legacy_make_node_id,
)

# ---------------------------------------------------------------------------
# Optional dependency guard (mirrors tree_sitter.py pattern per ADR-0002)
# ---------------------------------------------------------------------------

try:
    import tree_sitter  # noqa: F401

    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False

# ---------------------------------------------------------------------------
# Regex patterns for TypeScript exports (fallback path)
# ---------------------------------------------------------------------------

# Matches: export [default] [async] function|const|let|var|class|interface|type|enum NAME
_EXPORT_RE = re.compile(
    r"^export\s+"
    r"(?:default\s+)?"
    r"(?:async\s+)?"
    r"(?:function|const|let|var|class|interface|type|enum)\s+"
    r"(\w+)",
)

# ---------------------------------------------------------------------------
# Node ID builders are imported from :mod:`weld.strategies._tree_sitter_ids`
# (ADR 0041 § Layer 1). ``_make_node_id`` returns the canonical
# ``file:<rel_path_no_ext>`` form; ``_legacy_make_node_id`` returns the
# pre-migration shape for the ``aliases`` provenance list.
# ---------------------------------------------------------------------------
# Tree-sitter AST parsing (only called when TREE_SITTER_AVAILABLE is True)
# ---------------------------------------------------------------------------

def _load_ts_language(variant: str = "typescript") -> object:
    """Return the tree-sitter Language for the requested grammar variant.

    ``tree_sitter_typescript`` exposes ``language_typescript()`` for plain TS
    and ``language_tsx()`` for TSX. ``variant`` is ``"typescript"`` (default)
    or ``"tsx"``; unknown values fall through to the TS grammar.
    """
    import importlib

    try:
        mod = importlib.import_module("tree_sitter_typescript")
    except ImportError as exc:
        raise ImportError(
            "tree-sitter grammar for TypeScript not installed: "
            "pip install tree-sitter-typescript"
        ) from exc

    if variant == "tsx" and hasattr(mod, "language_tsx"):
        return mod.language_tsx()
    if hasattr(mod, "language_typescript"):
        return mod.language_typescript()
    if hasattr(mod, "language"):
        return mod.language()
    raise ImportError(
        "tree-sitter-typescript module does not expose a language function"
    )


def _ts_variant_for(path: Path) -> str:
    """Return the grammar variant key for a TypeScript/TSX file.

    Delegates to the shared table so this strategy and the ``tree_sitter``
    one cannot drift about which grammar reads a ``.tsx`` file -- they did,
    and the one that got it wrong is where gap G4 lived (bd lrnx1.5).
    """
    return _ts_dialect.grammar_variant(_ts_dialect.TYPESCRIPT_LANGUAGE, path)

def _load_ts_queries() -> dict[str, str]:
    """Load the tree-sitter query definitions for TypeScript.

    Delegates to the shared query loader in ``tree_sitter.py``.
    """
    from weld.strategies.tree_sitter import load_language_queries

    return load_language_queries("typescript")

def _parse_ts_symbols(
    file_path: Path,
    queries: dict[str, str],
    ts_lang: object,
) -> dict[str, list[str]]:
    """Parse a TypeScript file with tree-sitter and return extracted symbols.

    Args:
        file_path: Absolute path to the TypeScript/TSX file.
        queries: Dict of query name -> S-expression string.
        ts_lang: The tree-sitter Language object for TypeScript.

    Returns:
        Dict mapping query name to list of captured symbol names.
    """
    parser = tree_sitter.Parser(ts_lang)  # type: ignore[name-defined]
    source_bytes = file_path.read_bytes()
    tree = parser.parse(source_bytes)

    ts_language_obj = tree_sitter.Language(ts_lang)  # type: ignore[name-defined]
    result: dict[str, list[str]] = {}

    for qname, qstr in queries.items():
        names: list[str] = []
        try:
            query = ts_language_obj.query(qstr)
            matches = query.captures(tree.root_node)
            for node, capture_name in matches:
                if capture_name == "name":
                    names.append(node.text.decode("utf-8"))
        except Exception:
            # If a query fails at runtime, skip it gracefully
            pass
        result[qname] = names

    return result

# ---------------------------------------------------------------------------
# Regex extraction (fallback path)
# ---------------------------------------------------------------------------

def _extract_regex(source_text: str) -> list[str]:
    """Extract export names from TypeScript source using regex.

    Returns a list of exported symbol names. This is the fallback when
    tree-sitter is not available.
    """
    exports: list[str] = []
    for line in source_text.splitlines():
        stripped = line.strip()
        m = _EXPORT_RE.match(stripped)
        if m:
            exports.append(m.group(1))
    return exports

# ---------------------------------------------------------------------------
# Line counting helper
# ---------------------------------------------------------------------------

def _count_lines(source_text: str) -> int:
    """Count the number of lines in source text."""
    newlines = source_text.count("\n")
    return newlines + (1 if source_text and not source_text.endswith("\n") else 0)


def _build_file_node(
    rel_path: str,
    label: str,
    exports: list[str],
    line_count: int,
    confidence: str,
    *,
    classes: list[str] | None = None,
    imports: list[str] | None = None,
    aliases: list[str] | None = None,
) -> dict:
    """Assemble a file-type node dict shared by AST and regex paths."""
    props: dict = {
        "file": rel_path,
        "exports": exports,
        "line_count": line_count,
        "source_strategy": "typescript_exports",
        "authority": "derived",
        "confidence": confidence,
        "roles": ["implementation"],
        # ADR 0042: ``typescript_exports`` only iterates files matched
        # by the configured project glob, so every emitted file node
        # is project-origin.
        "origin": "project",
    }
    if classes:
        props["types"] = classes
    if imports:
        props["imports_from"] = imports
    if aliases:
        props["aliases"] = aliases
    return {"type": "file", "label": label, "props": props}


def _build_contains_edge(package_id: str, nid: str, confidence: str) -> dict:
    """Assemble a ``contains`` edge dict shared by AST and regex paths."""
    return {
        "from": package_id,
        "to": nid,
        "type": "contains",
        "props": {
            "source_strategy": "typescript_exports",
            "confidence": confidence,
        },
    }

# ---------------------------------------------------------------------------
# Strategy entry point
# ---------------------------------------------------------------------------

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract exported symbols from TypeScript / TSX modules.

    When tree-sitter is installed, uses AST-based extraction for richer
    results with ``confidence: "definite"``. Otherwise falls back to
    regex with ``confidence: "inferred"``.
    """
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source["glob"]
    excludes = source.get("exclude", [])
    package_id = source.get("package", "")
    id_prefix = source.get("id_prefix", "")

    matched, dirs = resolve_glob_with_provenance(root, pattern, excludes)
    discovered_from.extend(dirs)

    if not matched:
        return StrategyResult(nodes, edges, discovered_from)

    # Grammars are loaded lazily per variant (typescript vs tsx) and cached.
    ts_lang_cache: dict[str, object] = {}
    ts_queries: dict[str, str] | None = None
    use_ast = TREE_SITTER_AVAILABLE
    if use_ast:
        try:
            ts_queries = _load_ts_queries()
        except (ImportError, FileNotFoundError, ValueError):
            use_ast = False

    def _grammar_for(variant: str) -> object | None:
        if variant in ts_lang_cache:
            return ts_lang_cache[variant]
        try:
            ts_lang_cache[variant] = _load_ts_language(variant)
        except (ImportError, FileNotFoundError, ValueError):
            return None
        return ts_lang_cache[variant]

    for ts_file in matched:
        if not ts_file.is_file():
            continue
        try:
            source_text = ts_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        rel_path = rel_to_root(ts_file, root)
        line_count = _count_lines(source_text)

        # Try AST extraction first, fall back to regex on failure
        ts_lang = _grammar_for(_ts_variant_for(ts_file)) if use_ast else None
        if use_ast and ts_queries is not None and ts_lang is not None:
            try:
                symbols = _parse_ts_symbols(ts_file, ts_queries, ts_lang)
                exports = symbols.get("exports", [])
                if not exports:
                    continue
                nid = _make_node_id(rel_path, id_prefix)
                legacy_nid = _legacy_make_node_id(rel_path, id_prefix)
                aliases = sorted({legacy_nid} - {nid})
                nodes[nid] = _build_file_node(
                    rel_path, ts_file.stem, exports, line_count, "definite",
                    classes=symbols.get("classes", []),
                    imports=symbols.get("imports", []),
                    aliases=aliases,
                )
                if package_id:
                    edges.append(_build_contains_edge(package_id, nid, "definite"))
                continue  # Successfully used AST, skip regex path
            except Exception:
                pass  # AST parsing failed; fall through to regex

        # Regex fallback path
        exports = _extract_regex(source_text)
        if not exports:
            continue
        nid = _make_node_id(rel_path, id_prefix)
        legacy_nid = _legacy_make_node_id(rel_path, id_prefix)
        aliases = sorted({legacy_nid} - {nid})
        nodes[nid] = _build_file_node(
            rel_path, ts_file.stem, exports, line_count, "inferred",
            aliases=aliases,
        )
        if package_id:
            edges.append(_build_contains_edge(package_id, nid, "inferred"))

    return StrategyResult(nodes, edges, discovered_from)
