"""Strategy: Universal symbol extraction via tree-sitter.

Uses tree-sitter Python bindings to extract exports, class/type
definitions, import targets, and line counts from any language with
a supported grammar.  Per-language query patterns are bundled as
YAML files in ``cortex/languages/{language}.yaml``.

Tree-sitter is an optional pip dependency.  When not installed the
strategy degrades gracefully: returns empty results and appends a
clear install-instruction warning to ``context["_warnings"]``.

See: cortex/docs/adr/0002-tree-sitter-optional-dependency.md
"""

from __future__ import annotations

from pathlib import Path

from cortex._yaml import parse_yaml
from cortex.strategies import cpp_resolver as _cpp_resolver
from cortex.strategies._helpers import StrategyResult, filter_glob_results, should_skip

# ---------------------------------------------------------------------------
# Optional dependency guard
# ---------------------------------------------------------------------------

try:
    import tree_sitter  # noqa: F401

    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False

_INSTALL_MSG = (
    "tree_sitter strategy requires: "
    "pip install tree-sitter tree-sitter-python "
    "tree-sitter-typescript tree-sitter-go tree-sitter-rust "
    "tree-sitter-cpp"
)

# ---------------------------------------------------------------------------
# Language query file loading
# ---------------------------------------------------------------------------

def _languages_dir() -> Path:
    """Return the path to the bundled language query files.

    Resolves relative to this module's location: ``../languages/``.
    """
    return Path(__file__).resolve().parent.parent / "languages"

def load_language_queries(language: str) -> dict[str, str]:
    """Load tree-sitter query strings for *language* from YAML.

    Args:
        language: Language name matching a file in ``cortex/languages/``.

    Returns:
        Dict mapping query name (e.g. "exports") to S-expression string.

    Raises:
        FileNotFoundError: No query file for *language*.
        ValueError: Query file exists but is malformed.
    """
    lang_dir = _languages_dir()
    query_file = lang_dir / f"{language}.yaml"
    if not query_file.exists():
        raise FileNotFoundError(
            f"No tree-sitter query file for language '{language}': "
            f"expected {query_file}"
        )

    text = query_file.read_text(encoding="utf-8")
    try:
        data = parse_yaml(text)
    except Exception as exc:
        raise ValueError(
            f"Malformed query file {query_file.name}: {exc}"
        ) from exc

    if not isinstance(data, dict) or "queries" not in data:
        raise ValueError(
            f"Malformed query file {query_file.name}: "
            f"missing 'queries' key"
        )

    queries = data["queries"]
    if not isinstance(queries, dict):
        raise ValueError(
            f"Malformed query file {query_file.name}: "
            f"'queries' must be a mapping"
        )

    # Validate each query is a non-empty string
    result: dict[str, str] = {}
    for name, query_str in queries.items():
        if not isinstance(query_str, str) or not query_str.strip():
            raise ValueError(
                f"Malformed query file {query_file.name}: "
                f"query '{name}' must be a non-empty string"
            )
        result[name] = query_str.strip()

    return result

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
        for f in filter_glob_results(root, raw):
            files.append(f)
            dirs.add(str(f.parent.relative_to(root)) + "/")
    else:
        parent = (root / pattern).parent
        if not parent.is_dir():
            return [], []
        name_pat = Path(pattern).name
        raw = sorted(parent.glob(name_pat))
        for f in filter_glob_results(root, raw):
            files.append(f)
        dirs.add(str(parent.relative_to(root)) + "/")

    return files, sorted(dirs)

# ---------------------------------------------------------------------------
# Node ID builder (mirrors python_module._make_node_id)
# ---------------------------------------------------------------------------

def _make_node_id(rel_path: str, id_prefix: str) -> str:
    """Build a unique node ID from the relative path."""
    p = Path(rel_path)
    stem = p.stem
    if id_prefix:
        parts = p.parts
        anchor_idx = None
        for i, part in enumerate(parts):
            if part == id_prefix:
                anchor_idx = i
        if anchor_idx is not None:
            sub_parts = list(parts[anchor_idx + 1:])
            if sub_parts:
                sub_parts[-1] = stem
            else:
                sub_parts = [stem]
            return f"file:{id_prefix}/{'/'.join(sub_parts)}"
        return f"file:{id_prefix}/{stem}"
    return f"file:{stem}"

# ---------------------------------------------------------------------------
# Tree-sitter parsing (only called when TREE_SITTER_AVAILABLE is True)
# ---------------------------------------------------------------------------

def _load_ts_language(language: str) -> object:
    """Dynamically import and return the tree-sitter Language for *language*.

    The grammar packages follow the naming convention
    ``tree_sitter_{language}``.
    """
    import importlib

    module_name = f"tree_sitter_{language}"
    try:
        mod = importlib.import_module(module_name)
    except ImportError as exc:
        raise ImportError(
            f"tree-sitter grammar for '{language}' not installed: "
            f"pip install {module_name.replace('_', '-')}"
        ) from exc

    # Modern tree-sitter-python grammars expose a language() function
    if hasattr(mod, "language"):
        return mod.language()
    raise ImportError(
        f"tree-sitter grammar module '{module_name}' does not expose "
        f"a language() function"
    )

def _parse_file_symbols(
    file_path: Path,
    language: str,
    queries: dict[str, str],
) -> dict[str, list[str]]:
    """Parse a source file with tree-sitter and return extracted symbols.

    Args:
        file_path: Absolute path to the source file.
        language: Language name (must match a grammar package).
        queries: Dict of query name -> S-expression string.

    Returns:
        Dict mapping query name to list of captured symbol names.
    """
    ts_lang = _load_ts_language(language)
    ts_language_obj = tree_sitter.Language(ts_lang)  # type: ignore[name-defined]
    parser = tree_sitter.Parser(ts_language_obj)  # type: ignore[name-defined]

    source_bytes = file_path.read_bytes()
    tree = parser.parse(source_bytes)
    result: dict[str, list[str]] = {}

    for qname, qstr in queries.items():
        names: list[str] = []
        try:
            query = tree_sitter.Query(ts_language_obj, qstr)  # type: ignore[name-defined]
            cursor = tree_sitter.QueryCursor(query)  # type: ignore[name-defined]
            for _pattern_idx, capture_dict in cursor.matches(tree.root_node):
                for node in capture_dict.get("name", []):
                    names.append(node.text.decode("utf-8"))
        except Exception:
            # If a query fails at runtime, skip it gracefully
            pass
        result[qname] = names

    return result

# ---------------------------------------------------------------------------
# Best-effort tree-sitter call graph extraction
# ---------------------------------------------------------------------------
#
# Per ADR ``cortex/docs/adr/0004-call-graph-schema-extension.md`` we extract
# call edges from tree-sitter grammars on a strict best-effort basis. We
# emit:
#
#   * One ``symbol`` node per function/method definition (using a stable
#     ``symbol:<lang>:<module>:<qualname>`` id). The "qualname" we have
#     here is just the function's identifier text -- tree-sitter does
#     not give us nesting context for free.
#   * One ``calls`` edge per call site, pointing at a
#     ``symbol:unresolved:<name>`` sentinel. Cross-symbol resolution
#     across files / imports is explicitly out of scope.
#
# This degrades gracefully: if the grammar lacks the ``calls`` query the
# helper returns ``([], [])`` and the rest of the strategy is unaffected.

def _ts_module_from_path(rel_path: str) -> str:
    """Return a stable module-ish path for use in symbol ids."""
    p = Path(rel_path)
    parts = list(p.parts)
    if not parts:
        return ""
    parts[-1] = p.stem
    return ".".join(parts)

def _extract_call_edges(
    file_path: Path,
    rel_path: str,
    language: str,
    queries: dict[str, str],
) -> tuple[dict[str, dict], list[dict]]:
    """Run the ``calls`` query and emit symbol nodes + ``calls`` edges.

    Returns ``(nodes, edges)``. ``nodes`` and ``edges`` may be empty if
    the language file has no ``calls`` query or the parser fails.
    """
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    if "calls" not in queries:
        return nodes, edges

    try:
        ts_lang = _load_ts_language(language)
        ts_language_obj = tree_sitter.Language(ts_lang)  # type: ignore[name-defined]
        parser = tree_sitter.Parser(ts_language_obj)  # type: ignore[name-defined]
        source_bytes = file_path.read_bytes()
        tree = parser.parse(source_bytes)
    except Exception:
        return nodes, edges

    module_path = _ts_module_from_path(rel_path)

    # Definitions: capture every function/method def name as a symbol node.
    def_query_str = queries.get("exports", "")
    definitions: list[str] = []
    if def_query_str:
        try:
            dq = tree_sitter.Query(ts_language_obj, def_query_str)  # type: ignore[name-defined]
            dc = tree_sitter.QueryCursor(dq)  # type: ignore[name-defined]
            for _pi, caps in dc.matches(tree.root_node):
                for n in caps.get("name", []):
                    definitions.append(n.text.decode("utf-8"))
        except Exception:
            pass

    for name in definitions:
        sid = f"symbol:{language}:{module_path}:{name}"
        nodes.setdefault(
            sid,
            {
                "type": "symbol",
                "label": name,
                "props": {
                    "file": rel_path,
                    "module": module_path,
                    "qualname": name,
                    "language": language,
                    "source_strategy": "tree_sitter",
                    "authority": "derived",
                    "confidence": "definite",
                    "roles": ["implementation"],
                },
            },
        )

    # Caller fallback symbol when we cannot attribute a call to a
    # specific enclosing definition. Tree-sitter does not give us scope
    # tracking for free, so for the smoke-test surface we emit a single
    # module-level "<file>" symbol that owns every call site in the file.
    file_caller_id = f"symbol:{language}:{module_path}:<file>"
    nodes.setdefault(
        file_caller_id,
        {
            "type": "symbol",
            "label": f"{module_path}",
            "props": {
                "file": rel_path,
                "module": module_path,
                "qualname": "<file>",
                "language": language,
                "scope": "module",
                "source_strategy": "tree_sitter",
                "authority": "derived",
                "confidence": "inferred",
                "roles": ["implementation"],
            },
        },
    )

    # Calls
    try:
        cq = tree_sitter.Query(ts_language_obj, queries["calls"])  # type: ignore[name-defined]
        cc = tree_sitter.QueryCursor(cq)  # type: ignore[name-defined]
        seen: set[str] = set()
        for _pi, caps in cc.matches(tree.root_node):
            for n in caps.get("name", []):
                callee = n.text.decode("utf-8")
                if not callee or callee in seen:
                    continue
                seen.add(callee)
                target = f"symbol:unresolved:{callee}"
                nodes.setdefault(
                    target,
                    {
                        "type": "symbol",
                        "label": callee,
                        "props": {
                            "qualname": callee,
                            "language": language,
                            "resolved": False,
                            "source_strategy": "tree_sitter",
                            "authority": "derived",
                            "confidence": "speculative",
                            "roles": ["implementation"],
                        },
                    },
                )
                edges.append(
                    {
                        "from": file_caller_id,
                        "to": target,
                        "type": "calls",
                        "props": {
                            "source_strategy": "tree_sitter",
                            "confidence": "speculative",
                            "resolved": False,
                        },
                    }
                )
    except Exception:
        pass

    return nodes, edges

# ---------------------------------------------------------------------------
# C++ cross-file include resolver (layer 2; project-f7y.2)
# ---------------------------------------------------------------------------
#
# The full implementation lives in ``cortex.strategies.cpp_resolver`` so this
# module stays inside its line-count budget. We re-export the
# resolver-private symbol names that the tests patch so the public
# surface of ``tree_sitter`` is unchanged.

# Re-exports kept for tests/back-compat. Underscored to signal "internal".
_resolve_cpp_include = _cpp_resolver.resolve_cpp_include
_cpp_match_callee = _cpp_resolver.match_callee
_resolve_cpp_includes_pass = _cpp_resolver.resolve_includes_pass

# ---------------------------------------------------------------------------
# Strategy entry point
# ---------------------------------------------------------------------------

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract symbols from source files using tree-sitter.

    When tree-sitter is not installed, returns empty results and appends
    a warning to ``context["_warnings"]`` with install instructions.
    """
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    if not TREE_SITTER_AVAILABLE:
        context.setdefault("_warnings", []).append(
            "tree-sitter not installed; skipping tree_sitter strategy. "
            f"Install with: {_INSTALL_MSG}"
        )
        return StrategyResult(nodes, edges, discovered_from)

    language = source.get("language")
    if not language:
        context.setdefault("_warnings", []).append(
            "tree_sitter strategy requires a 'language' field in the "
            "source entry (e.g. language: python)"
        )
        return StrategyResult(nodes, edges, discovered_from)

    # Load query patterns for this language
    try:
        queries = load_language_queries(language)
    except (FileNotFoundError, ValueError) as exc:
        context.setdefault("_warnings", []).append(str(exc))
        return StrategyResult(nodes, edges, discovered_from)

    pattern = source["glob"]
    excludes = source.get("exclude", [])
    package_id = source.get("package", "")
    id_prefix = source.get("id_prefix", "")
    emit_calls = bool(source.get("emit_calls", False))

    matched, dirs = _resolve_glob(root, pattern)
    discovered_from.extend(dirs)

    if not matched:
        return StrategyResult(nodes, edges, discovered_from)

    # State accumulator for the C++ cross-file include resolver pass.
    cpp_per_file: list[dict] = []

    for fpath in matched:
        if not fpath.is_file():
            continue
        if should_skip(fpath, excludes):
            continue
        try:
            source_text = fpath.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        rel_path = str(fpath.relative_to(root))

        # Parse symbols using tree-sitter
        symbols = _parse_file_symbols(fpath, language, queries)

        # Optional: also emit function-level call graph nodes/edges.
        # Gated on the per-source ``emit_calls`` flag so existing
        # ``tree_sitter`` source entries do not change behaviour.
        if emit_calls:
            cg_nodes, cg_edges = _extract_call_edges(
                fpath, rel_path, language, queries
            )
            nodes.update(cg_nodes)
            edges.extend(cg_edges)

        # Track per-file state for the cpp include resolver (layer 2).
        if language == "cpp":
            module_path = _ts_module_from_path(rel_path)
            cpp_per_file.append(
                {
                    "abs_path": fpath,
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

        exports = symbols.get("exports", [])
        if not exports:
            continue

        nid = _make_node_id(rel_path, id_prefix)
        newlines = source_text.count("\n")
        line_count = newlines + (
            1 if source_text and not source_text.endswith("\n") else 0
        )

        node_props: dict = {
            "file": rel_path,
            "exports": exports,
            "line_count": line_count,
        }

        # Include class/type definitions if present
        classes = symbols.get("classes", [])
        if classes:
            node_props["types"] = classes

        # Include imports if present
        imports = symbols.get("imports", [])
        if imports:
            node_props["imports_from"] = imports

        node_props["source_strategy"] = "tree_sitter"
        node_props["authority"] = "derived"
        node_props["confidence"] = "definite"
        node_props["roles"] = ["implementation"]

        nodes[nid] = {
            "type": "file",
            "label": fpath.stem,
            "props": node_props,
        }

        if package_id:
            edges.append(
                {
                    "from": package_id,
                    "to": nid,
                    "type": "contains",
                    "props": {
                        "source_strategy": "tree_sitter",
                        "confidence": "definite",
                    },
                }
            )

    # Layer 2 (cpp only): rewrite unresolved sentinels across includes.
    if language == "cpp" and emit_calls and cpp_per_file:
        # The configured glob may not have covered headers (a common
        # case is ``**/*.cpp``), so do a side-channel header walk to
        # populate the include resolver's symbol index.  We only
        # surface header parses to the resolver — we do NOT add file
        # nodes for them, so the rest of the graph is unchanged.
        def _parse_for_resolver(file_path: Path, lang: str) -> dict:
            return _parse_file_symbols(file_path, lang, queries)

        _cpp_resolver.augment_state_with_headers(
            root,
            cpp_per_file,
            language,
            excludes,
            _parse_for_resolver,
        )
        _cpp_resolver.resolve_includes_pass(
            root, cpp_per_file, nodes, edges
        )

    return StrategyResult(nodes, edges, discovered_from)
