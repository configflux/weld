"""Best-effort tree-sitter call graph extraction.

Factored out of ``weld.strategies.tree_sitter`` so the strategy module
stays within the 400-line default cap.

Per ADR ``weld/docs/adr/0004-call-graph-schema-extension.md`` we extract
call edges from tree-sitter grammars on a strict best-effort basis. We
emit:

  * One ``symbol`` node per function/method definition (using a stable
    ``symbol:<lang>:<module>:<qualname>`` id). The "qualname" we have
    here is just the function's identifier text -- tree-sitter does
    not give us nesting context for free.
  * One ``calls`` edge per call site, pointing at a
    ``symbol:unresolved:<name>`` sentinel. Cross-symbol resolution
    across files / imports is explicitly out of scope.

This degrades gracefully: if the grammar lacks the ``calls`` query the
helper returns ``([], [])`` and the rest of the strategy is unaffected.

For TypeScript and JavaScript both halves of that shape are sharpened,
per ADR 0142 D2 (bd lrnx1.3): a call site is attributed to the enclosing
definition the same file exports when the grammar shows one, and a
callee whose name came from a named import carries the import as an edge
prop so :mod:`weld._graph_closure_ts_calls` can bind it against the whole
merged graph. Both readings live in
:mod:`weld.strategies._ts_call_sites`; what stays here is the pass that
walks the grammar. Every other language keeps the file-sentinel caller
and the bare sentinel callee it always had.
"""

from __future__ import annotations

from pathlib import Path

from weld.strategies._ts_call_sites import (
    CALL_SITE_LANGUAGES,
    TS_IMPORT_PROP,
    enclosing_definition,
    import_bindings,
    import_hint_props,
    unresolved_sentinel_node,
)
from weld.strategies._ts_parse import ParseCache, ParseEntry, load_ts_language


def ts_module_from_path(rel_path: str) -> str:
    """Return a stable module-ish path for use in symbol ids."""
    p = Path(rel_path)
    parts = list(p.parts)
    if not parts:
        return ""
    parts[-1] = p.stem
    return ".".join(parts)


def extract_call_edges(
    file_path: Path,
    rel_path: str,
    language: str,
    queries: dict[str, str],
    *,
    cache: ParseCache | None = None,
) -> tuple[dict[str, dict], list[dict]]:
    """Run the ``calls`` query and emit symbol nodes + ``calls`` edges.

    Args:
        file_path: Absolute path to the source file.
        rel_path: Project-root-relative path used in symbol ids and
            ``provenance.file``.
        language: Language name matching a tree-sitter grammar.
        queries: Dict of query name -> S-expression string (from
            ``weld/languages/<language>.yaml``).
        cache: Optional :class:`weld.strategies._ts_parse.ParseCache`
            populated by an earlier ``parse_file_symbols`` call. When
            supplied and the cache holds an entry for
            ``(file_path, mtime, language)``, the cached
            :class:`tree_sitter.Tree` and source bytes are reused --
            avoiding a duplicate parse on hot path 2 of the C# discover
            cProfile baseline. On miss the helper still parses (and
            stores the entry so a follow-up consumer hits the cache).
            Omitting ``cache`` preserves the original no-cache
            behaviour for callers that have not threaded one through.

    Returns ``(nodes, edges)``. ``nodes`` and ``edges`` may be empty if
    the language file has no ``calls`` query or the parser fails.
    """
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    # Guarded like every other lazy tree-sitter import (ADR 0002): callers
    # can reach this with a mocked-in parser, so absence must degrade to
    # empty results, never escape (bd uaz2d).
    try:
        import tree_sitter  # noqa: F811
    except ImportError:
        return nodes, edges

    if "calls" not in queries:
        return nodes, edges

    try:
        ts_language_obj, tree = _resolve_parse(
            file_path, language, cache, tree_sitter,
        )
    except Exception:
        return nodes, edges

    module_path = ts_module_from_path(rel_path)

    # Definitions: capture implementation definitions as symbol nodes.
    definitions: list[str] = []
    for query_name in _definition_query_names(language):
        def_query_str = queries.get(query_name, "")
        if not def_query_str:
            continue
        try:
            if cache is not None:
                dq = cache.get_or_compile_query(
                    language, query_name, def_query_str,
                    ts_language_obj, tree_sitter,
                )
            else:
                dq = tree_sitter.Query(ts_language_obj, def_query_str)
            dc = tree_sitter.QueryCursor(dq)
            for _pi, caps in dc.matches(tree.root_node):
                for n in caps.get("name", []):
                    definitions.append(n.text.decode("utf-8"))
        except Exception:
            pass

    for name in definitions:
        sid = f"symbol:{language}:{module_path}:{name}"
        props: dict = {
            "file": rel_path,
            "module": module_path,
            "qualname": name,
            "language": language,
            "source_strategy": "tree_sitter",
            "authority": "derived",
            "confidence": "definite",
            "roles": ["implementation"],
            # ADR 0042: layer-1 only iterates project files, so every
            # definition symbol minted here is project-origin (this
            # holds for every language, not just C++).
            "origin": "project",
        }
        nodes.setdefault(
            sid,
            {
                "type": "symbol",
                "label": name,
                "props": props,
            },
        )

    # Caller fallback symbol when we cannot attribute a call to a
    # specific enclosing definition. Tree-sitter does not give us scope
    # tracking for free, so for the smoke-test surface we emit a single
    # module-level "<file>" symbol that owns every call site in the file.
    file_caller_id = f"symbol:{language}:{module_path}:<file>"
    file_caller_props: dict = {
        "file": rel_path,
        "module": module_path,
        "qualname": "<file>",
        "language": language,
        "scope": "module",
        # ADR 0064 criterion 2: every symbol carries a documented
        # ``kind``. The file-level caller is a *synthetic* weld
        # modelling node (not a real source-code symbol), so its kind
        # is the synthetic ``"file"`` -- declared in
        # ``tools/tier_check_kinds._SYNTHETIC_KINDS`` so the
        # criterion-1 vocabulary tally filters it out.
        "kind": "file",
        "source_strategy": "tree_sitter",
        "authority": "derived",
        "confidence": "inferred",
        "roles": ["implementation"],
        # ADR 0042: layer-1 iterates project files only, so the
        # synthetic file-level caller belongs to the project tree for
        # every language we support.
        "origin": "project",
    }
    nodes.setdefault(
        file_caller_id,
        {
            "type": "symbol",
            "label": f"{module_path}",
            "props": file_caller_props,
        },
    )

    # TS / JS only (ADR 0142 D2): what a call site is written inside, and
    # which import introduced its callee's name. Both are read from the tree
    # this pass already holds; every other language skips them and keeps the
    # shape above.
    attributes_callers = language in CALL_SITE_LANGUAGES
    definition_names = frozenset(definitions)
    bindings = (
        import_bindings(
            tree, language, ts_language_obj, queries, tree_sitter, cache=cache,
        )
        if attributes_callers
        else {}
    )

    # Calls
    try:
        if cache is not None:
            cq = cache.get_or_compile_query(
                language, "calls", queries["calls"],
                ts_language_obj, tree_sitter,
            )
        else:
            cq = tree_sitter.Query(ts_language_obj, queries["calls"])
        cc = tree_sitter.QueryCursor(cq)
        # Keyed on ``(caller, callee)`` rather than on the callee alone: with
        # per-definition attribution one file legitimately calls the same
        # function from two of its own exports, and a file-wide key would
        # record only whichever the grammar reached first. For every language
        # that does not attribute, the caller is constant and this is the
        # same key it always was.
        seen: set[tuple[str, str]] = set()
        for _pi, caps in cc.matches(tree.root_node):
            for n in caps.get("name", []):
                callee = n.text.decode("utf-8")
                if not callee:
                    continue
                caller_id = file_caller_id
                if attributes_callers:
                    enclosing = enclosing_definition(n, definition_names)
                    if enclosing:
                        caller_id = f"symbol:{language}:{module_path}:{enclosing}"
                if (caller_id, callee) in seen:
                    continue
                seen.add((caller_id, callee))
                target = f"symbol:unresolved:{callee}"
                nodes.setdefault(
                    target, unresolved_sentinel_node(callee, language),
                )
                edge_props: dict = {
                    "source_strategy": "tree_sitter",
                    "confidence": "speculative",
                    "resolved": False,
                    "raw": callee,
                    "resolution": "unresolved",
                    "provenance": _provenance(rel_path, n),
                }
                binding = bindings.get(callee)
                if binding is not None:
                    exported, specifier = binding
                    edge_props[TS_IMPORT_PROP] = import_hint_props(
                        callee, exported, specifier,
                    )
                edges.append(
                    {
                        "from": caller_id,
                        "to": target,
                        "type": "calls",
                        "props": edge_props,
                    }
                )
    except Exception:
        pass

    return nodes, edges


def _resolve_parse(
    file_path: Path,
    language: str,
    cache: ParseCache | None,
    tree_sitter_mod,
):
    """Return ``(language_obj, tree)`` for ``file_path``, reusing cache.

    Hot path 2 elimination: when ``cache`` is supplied and already holds
    a :class:`ParseEntry` for ``(file_path, mtime, language)``, return
    the cached tree directly. Otherwise load the grammar (memoized via
    ``cache`` when supplied), parse the file, and -- if a cache was
    supplied -- store the new entry so subsequent callers also hit it.
    Raises whatever ``tree_sitter`` / ``read_bytes`` raises; the caller
    swallows in its existing ``except Exception``.
    """
    if cache is not None:
        entry = cache.get_parse(file_path, language)
        if entry is not None:
            return entry.language_obj, entry.tree
        language_obj, parser = cache.get_or_load_language(
            language, load_ts_language, tree_sitter_mod,
        )
        source_bytes = file_path.read_bytes()
        tree = parser.parse(source_bytes)
        cache.store_parse(
            file_path, language,
            ParseEntry(
                tree=tree,
                source_bytes=source_bytes,
                language_obj=language_obj,
                parser=parser,
            ),
        )
        return language_obj, tree

    ts_lang = load_ts_language(language)
    language_obj = tree_sitter_mod.Language(ts_lang)
    parser = tree_sitter_mod.Parser(language_obj)
    source_bytes = file_path.read_bytes()
    tree = parser.parse(source_bytes)
    return language_obj, tree


def _provenance(rel_path: str, node) -> dict:
    """Return deterministic file/line provenance for a captured node."""
    provenance = {"file": rel_path}
    point = getattr(node, "start_point", None)
    if point is None:
        point = getattr(node, "startPosition", None)
    if isinstance(point, tuple) and point:
        provenance["line"] = int(point[0]) + 1
        return provenance
    row = getattr(point, "row", None)
    if row is not None:
        provenance["line"] = int(row) + 1
    return provenance


def _definition_query_names(language: str) -> tuple[str, ...]:
    if language == "csharp":
        # ADR 0064 § 1: C# type-like declarations are split per
        # decl-kind in ``weld/languages/csharp.yaml`` so the call-graph
        # layer must iterate every bucket -- otherwise interface,
        # struct, and record declarations never mint definition symbols
        # and ``calls`` edges into them fall back to unresolved
        # sentinels.
        return ("classes", "interfaces", "structs", "records", "methods", "properties")
    return ("exports",)
