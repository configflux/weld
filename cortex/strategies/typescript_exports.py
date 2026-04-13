"""Strategy: Exported symbols from TypeScript / TSX modules.

Uses tree-sitter for AST-based extraction when available, falling back to
regex-based extraction otherwise.

AST path (tree-sitter available):
- Parses TypeScript files using tree-sitter and the query definitions from
  ``cortex/languages/typescript.yaml``
- Produces richer nodes: exports list, types (classes/interfaces), imports
- Emits ``confidence: "definite"`` for AST-confirmed exports

Regex fallback path (tree-sitter unavailable):
- Uses line-level regex to find ``export function|class|const|...``
- Cannot capture re-exports, barrel files, or dynamic exports
- Emits ``confidence: "inferred"``

See: cortex/docs/adr/0002-tree-sitter-optional-dependency.md

"""

from __future__ import annotations

import re
from pathlib import Path

from cortex.strategies._helpers import StrategyResult, filter_glob_results, should_skip

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
# Glob resolution (mirrors python_module._resolve_glob)
# ---------------------------------------------------------------------------

def _resolve_glob(root: Path, pattern: str) -> tuple[list[Path], list[str]]:
    """Resolve a glob pattern that may contain ``**``.

    Returns ``(matched_files, discovered_from_dirs)``.
    Results inside excluded or nested-repo-copy directories are filtered out.
    """
    files: list[Path] = []
    dirs: set[str] = set()

    if "**" in pattern:
        raw = sorted(root.glob(pattern))
        for ts in filter_glob_results(root, raw):
            files.append(ts)
            dirs.add(str(ts.parent.relative_to(root)) + "/")
    else:
        parent = (root / pattern).parent
        if not parent.is_dir():
            return [], []
        name_pat = Path(pattern).name
        raw = sorted(parent.glob(name_pat))
        for ts in filter_glob_results(root, raw):
            files.append(ts)
        dirs.add(str(parent.relative_to(root)) + "/")

    return files, sorted(dirs)

# ---------------------------------------------------------------------------
# Node ID builder (mirrors python_module._make_node_id)
# ---------------------------------------------------------------------------

def _make_node_id(rel_path: str, id_prefix: str) -> str:
    """Build a unique node ID from the relative path.

    If ``id_prefix`` is set (e.g. ``"web"``), the ID becomes
    ``file:web/subpath/stem``.  Without ``id_prefix``, falls back
    to ``file:{stem}``.
    """
    p = Path(rel_path)
    stem = p.stem
    if id_prefix:
        parts = p.parts
        anchor_idx = None
        for i, part in enumerate(parts):
            if part == id_prefix:
                anchor_idx = i
        if anchor_idx is not None:
            sub_parts = list(parts[anchor_idx + 1 :])
            if sub_parts:
                sub_parts[-1] = stem
            else:
                sub_parts = [stem]
            return f"file:{id_prefix}/{'/'.join(sub_parts)}"
        return f"file:{id_prefix}/{stem}"
    return f"file:{stem}"

# ---------------------------------------------------------------------------
# Tree-sitter AST parsing (only called when TREE_SITTER_AVAILABLE is True)
# ---------------------------------------------------------------------------

def _load_ts_language() -> object:
    """Dynamically import and return the tree-sitter Language for TypeScript.

    The grammar package is ``tree_sitter_typescript``.
    """
    import importlib

    try:
        mod = importlib.import_module("tree_sitter_typescript")
    except ImportError as exc:
        raise ImportError(
            "tree-sitter grammar for TypeScript not installed: "
            "pip install tree-sitter-typescript"
        ) from exc

    # Modern grammars expose a language() function; typescript has
    # language_typescript() for .ts and language_tsx() for .tsx
    if hasattr(mod, "language_typescript"):
        return mod.language_typescript()
    if hasattr(mod, "language"):
        return mod.language()
    raise ImportError(
        "tree-sitter-typescript module does not expose a language function"
    )

def _load_ts_queries() -> dict[str, str]:
    """Load the tree-sitter query definitions for TypeScript.

    Delegates to the shared query loader in ``tree_sitter.py``.
    """
    from cortex.strategies.tree_sitter import load_language_queries

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

    matched, dirs = _resolve_glob(root, pattern)
    discovered_from.extend(dirs)

    if not matched:
        return StrategyResult(nodes, edges, discovered_from)

    # Attempt to set up tree-sitter once for all files
    ts_lang = None
    ts_queries: dict[str, str] | None = None
    use_ast = TREE_SITTER_AVAILABLE

    if use_ast:
        try:
            ts_lang = _load_ts_language()
            ts_queries = _load_ts_queries()
        except (ImportError, FileNotFoundError, ValueError):
            # Grammar or query file not available; fall back to regex
            use_ast = False

    for ts_file in matched:
        if not ts_file.is_file():
            continue
        if should_skip(ts_file, excludes):
            continue
        try:
            source_text = ts_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        rel_path = str(ts_file.relative_to(root))
        line_count = _count_lines(source_text)

        # Try AST extraction first, fall back to regex on failure
        if use_ast and ts_queries is not None and ts_lang is not None:
            try:
                symbols = _parse_ts_symbols(ts_file, ts_queries, ts_lang)
                exports = symbols.get("exports", [])
                if not exports:
                    continue

                nid = _make_node_id(rel_path, id_prefix)
                node_props: dict = {
                    "file": rel_path,
                    "exports": exports,
                    "line_count": line_count,
                    "source_strategy": "typescript_exports",
                    "authority": "derived",
                    "confidence": "definite",
                    "roles": ["implementation"],
                }

                # Include class/interface/type definitions if present
                classes = symbols.get("classes", [])
                if classes:
                    node_props["types"] = classes

                # Include import sources if present
                imports = symbols.get("imports", [])
                if imports:
                    node_props["imports_from"] = imports

                nodes[nid] = {
                    "type": "file",
                    "label": ts_file.stem,
                    "props": node_props,
                }
                if package_id:
                    edges.append(
                        {
                            "from": package_id,
                            "to": nid,
                            "type": "contains",
                            "props": {
                                "source_strategy": "typescript_exports",
                                "confidence": "definite",
                            },
                        }
                    )
                continue  # Successfully used AST, skip regex path
            except Exception:
                # AST parsing failed for this file; fall through to regex
                pass

        # Regex fallback path
        exports = _extract_regex(source_text)
        if not exports:
            continue

        nid = _make_node_id(rel_path, id_prefix)
        nodes[nid] = {
            "type": "file",
            "label": ts_file.stem,
            "props": {
                "file": rel_path,
                "exports": exports,
                "line_count": line_count,
                "source_strategy": "typescript_exports",
                "authority": "derived",
                "confidence": "inferred",
                "roles": ["implementation"],
            },
        }
        if package_id:
            edges.append(
                {
                    "from": package_id,
                    "to": nid,
                    "type": "contains",
                    "props": {
                        "source_strategy": "typescript_exports",
                        "confidence": "inferred",
                    },
                }
            )

    return StrategyResult(nodes, edges, discovered_from)
