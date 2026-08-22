"""Strategy: Universal symbol extraction via tree-sitter.

Uses tree-sitter Python bindings to extract exports, class/type
definitions, import targets, and line counts from any language with
a supported grammar.  Per-language query patterns are bundled as
YAML files in ``weld/languages/{language}.yaml``.

Tree-sitter is an optional pip dependency.  When not installed the
strategy degrades gracefully: returns empty results and appends a
clear install-instruction warning to ``context["_warnings"]``.

See: weld/docs/adr/0002-tree-sitter-optional-dependency.md
"""

from __future__ import annotations

from pathlib import Path

from weld._rel_path import rel_to_root
from weld._yaml import parse_yaml
from weld.strategies import cpp_resolver as _cpp_resolver
from weld.strategies._cpp_post_pass import run_cpp_post_pass as _run_cpp_post_pass
from weld.strategies._glob_resolve import resolve_glob_with_provenance
from weld.strategies._helpers import StrategyResult
from weld.strategies._tree_sitter_ids import (
    canonical_file_node_id as _make_node_id,
    legacy_file_node_id as _legacy_make_node_id,
)
from weld.strategies import (
    _cpp_tree_sitter,
    _csharp_tree_sitter,
    _go_inherits,
    _go_tree_sitter,
    _java_tree_sitter,
    _rust_inherits,
    _rust_tree_sitter,
    _ts_call_graph,
    _ts_definitions,
    _ts_doc_comments,
    _ts_parse,
    _typescript_inherits,
    _typescript_tree_sitter,
)

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
    "tree-sitter-cpp tree-sitter-c-sharp tree-sitter-java"
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
        language: Language name matching a file in ``weld/languages/``.

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

# Patchable re-exports of helpers extracted to other modules (tests mock
# these names on this module, so ``extract()`` must read via namespace).
# ADR 0041 Layer 1 (Node IDs) -> ``_tree_sitter_ids``; Layer 2 (cpp includes) -> ``cpp_resolver``.
_resolve_cpp_include = _cpp_resolver.resolve_cpp_include
_cpp_match_callee = _cpp_resolver.match_callee
_resolve_cpp_includes_pass = _cpp_resolver.resolve_includes_pass
_extract_call_edges = _ts_call_graph.extract_call_edges
_ts_module_from_path = _ts_call_graph.ts_module_from_path
_load_ts_language = _ts_parse.load_ts_language

# Per-language post-pass dispatch (ADR 0056 Wave 3 / ADR 0064 criterion 2).
_FINALISERS = {
    "csharp": _csharp_tree_sitter.finalise,
    "java": _java_tree_sitter.finalise,
    "cpp": _cpp_tree_sitter.finalise,
    "rust": _rust_inherits.finalise,  # ADR 0064 criterion 2: trait-impl edges
    "typescript": _typescript_inherits.finalise,  # criterion 2: extends/implements
    "go": _go_inherits.finalise,  # criterion 2: embedding inherits + iface implements
}


def _parse_file_symbols(
    file_path: Path, language: str, queries: dict[str, str], *,
    cache: "_ts_parse.ParseCache | None" = None,
) -> dict[str, list[str]]:
    """Forward to ``_ts_parse.parse_file_symbols`` keeping the mock seam.

    Tests mock ``_load_ts_language`` on this module; passing it through as
    ``_language_loader`` keeps that seam. ``cache`` opts into the
    per-discover parse memo.
    """
    return _ts_parse.parse_file_symbols(
        file_path, language, queries,
        _language_loader=_load_ts_language, cache=cache)

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
    source_strategy = str(source.get("source_strategy", "tree_sitter"))

    matched, dirs = resolve_glob_with_provenance(root, pattern, excludes)
    discovered_from.extend(dirs)

    if not matched:
        return StrategyResult(nodes, edges, discovered_from)

    # State accumulator for the C++ cross-file include resolver pass.
    cpp_per_file: list[dict] = []

    # ADR 0042: per-language manifests cached once per discovery run.
    # Each ``build_caches`` returns ``None`` for non-matching languages.
    ts_caches = _typescript_tree_sitter.build_caches(root, language)
    enricher_caches: dict = (_csharp_tree_sitter.build_caches(root, language) or _java_tree_sitter.build_caches(root, language) or _cpp_tree_sitter.build_caches(root, language) or _rust_inherits.build_caches(language) or _typescript_inherits.build_caches(language) or _go_inherits.build_caches(language) or {})
    go_module_path = _go_tree_sitter.load_module_path(root) if language == "go" else ""
    rust_cargo = _rust_tree_sitter.load_cargo_metadata(root, language)
    parse_cache = _ts_parse.get_parse_cache(context)

    for fpath in matched:
        if not fpath.is_file():
            continue
        try:
            source_text = fpath.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        rel_path = rel_to_root(fpath, root)

        # Parse symbols. A missing per-language grammar surfaces as an
        # ImportError from ``load_ts_language``; detecting it lazily
        # here keeps behaviour identical in normal Python envs and in
        # Bazel's hermetic sandbox. First failure emits one dedup'd
        # warning and we break -- subsequent files would fail the same.
        try:
            symbols = _parse_file_symbols(fpath, language, queries, cache=parse_cache)
        except ImportError:
            _ts_parse.append_missing_grammar_warning(language, context)
            break
        runtime_startup = (
            (language == "csharp" and _csharp_tree_sitter.is_startup_source(rel_path, source_text, symbols))
            or (language == "cpp" and _cpp_tree_sitter.is_startup_source(rel_path, source_text, symbols))
        )

        if emit_calls:
            cg_nodes, cg_edges = _extract_call_edges(fpath, rel_path, language, queries, cache=parse_cache)
            nodes.update(cg_nodes)
            edges.extend(cg_edges)

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
                    "file_caller_id": f"symbol:{language}:{module_path}:<file>",
                }
            )

        exports = symbols.get("exports", [])
        if not exports and not runtime_startup:
            continue

        nid = _make_node_id(rel_path, id_prefix)
        legacy_nid = _legacy_make_node_id(rel_path, id_prefix)
        aliases = sorted({legacy_nid} - {nid})
        newlines = source_text.count("\n")
        line_count = newlines + (
            1 if source_text and not source_text.endswith("\n") else 0
        )

        node_props: dict = {
            "file": rel_path,
            "exports": exports,
            "line_count": line_count,
        }
        if aliases:
            node_props["aliases"] = aliases

        classes = symbols.get("classes", [])
        if classes:
            node_props["types"] = classes

        imports = symbols.get("imports", [])
        if imports:
            # Go's raw capture is quote-wrapped (bd bt5m); clean it before
            # it reaches imports_from so Go matches every sibling
            # language's shape and stays matchable by an exact-string
            # consumer like package_import_resolver. Re-assigning the
            # local also feeds the cleaned strings into
            # stamp_import_origins below, so imports_origin's keys are
            # clean too instead of only imports_from.
            if language == "go":
                imports = _go_tree_sitter.strip_import_quotes(imports)
            node_props["imports_from"] = imports
            if language == "go":
                _go_tree_sitter.stamp_import_origins(node_props, imports, go_module_path)
            elif language == "rust":
                _rust_tree_sitter.stamp_import_origins(node_props, imports, rust_cargo)
        _ts_parse.stamp_type_uses(node_props, symbols)  # ADR 0061
        node_props["source_strategy"] = source_strategy
        node_props["authority"] = "derived"
        node_props["confidence"] = "definite"
        node_props["roles"] = ["implementation"]
        # ADR 0042: project file nodes are minted from the configured
        # project glob for every supported language, so always
        # project-origin (call-graph sentinels are set in ``_ts_call_graph``).
        node_props["origin"] = "project"

        if language == "rust":  # ADR 0064 criterion 2: stage trait-impls
            _rust_inherits.stage_trait_impls(
                enricher_caches.get("impl_records"), rel_path=rel_path, source_text=source_text)
        elif language == "typescript":  # criterion 2: stage extends/implements
            _typescript_inherits.stage_inheritance(
                enricher_caches.get("inherit_records"), rel_path=rel_path, source_text=source_text)
        elif language == "go":  # criterion 2: stage embedding + iface satisfaction
            _go_inherits.stage_file(
                enricher_caches.get("go_inherit_records"), rel_path=rel_path, source_text=source_text)

        language_enricher = {"cpp": _cpp_tree_sitter, "csharp": _csharp_tree_sitter, "java": _java_tree_sitter}.get(language)
        if language_enricher:
            language_enricher.enrich_file_node(
                nodes, edges, nid, node_props, symbols,
                source_text, source_strategy, **enricher_caches,
            )
        elif ts_caches is not None:
            _typescript_tree_sitter.enrich_file_node(
                nodes, edges, nid, node_props, symbols,
                source_text, source_strategy,
                root=root, **ts_caches,
            )

        nodes[nid] = {"type": "file", "label": fpath.stem, "props": node_props}

        if package_id:
            edges.append(
                {
                    "from": package_id,
                    "to": nid,
                    "type": "contains",
                    "props": {
                        "source_strategy": source_strategy,
                        "confidence": "definite",
                    },
                }
            )

        # bd 5038-009x (ADR 0118 follow-up): best-effort per-symbol doc
        # comment, for the languages with a registered convention (Go,
        # Rust today). Reuses the parse + compiled "exports" query this
        # file's `_parse_file_symbols` call already primed in
        # `parse_cache` moments ago -- no second parse. `None` for every
        # other language leaves `props.summary` absent, unchanged from
        # before this call existed.
        summaries = _ts_doc_comments.extract_definition_summaries(
            fpath, language, queries, cache=parse_cache,
        )
        def_nodes, def_edges = _ts_definitions.promote_definition_symbols(
            language=language, rel_path=rel_path, symbols=symbols,
            file_node_id=nid, source_strategy=source_strategy,
            summaries=summaries,
        )
        for def_id, def_node in def_nodes.items():
            nodes[def_id] = def_node
        edges.extend(def_edges)

    # Layer 2 (cpp only): rewrite unresolved sentinels + ADR 0057 Wave 2
    # header/source pairing edges, in :mod:`weld.strategies._cpp_post_pass`.
    if language == "cpp" and emit_calls and cpp_per_file:
        def _parse_for_resolver(file_path: Path, lang: str) -> dict:
            return _parse_file_symbols(file_path, lang, queries, cache=parse_cache)

        _run_cpp_post_pass(
            root, cpp_per_file, nodes, edges,
            language, excludes, _parse_for_resolver,
            source_strategy=source_strategy,
        )

    # Per-language inheritance post-pass (ADR 0056 Wave 3 / ADR 0064 criterion 2).
    finalise = _FINALISERS.get(language)
    if finalise is not None:
        finalise(nodes, edges, enricher_caches, source_strategy)

    return StrategyResult(nodes, edges, discovered_from)
