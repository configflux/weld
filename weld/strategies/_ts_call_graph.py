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
"""

from __future__ import annotations

from pathlib import Path

from weld.strategies._ts_parse import load_ts_language


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
) -> tuple[dict[str, dict], list[dict]]:
    """Run the ``calls`` query and emit symbol nodes + ``calls`` edges.

    Returns ``(nodes, edges)``. ``nodes`` and ``edges`` may be empty if
    the language file has no ``calls`` query or the parser fails.
    """
    import tree_sitter  # noqa: F811

    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    if "calls" not in queries:
        return nodes, edges

    try:
        ts_lang = load_ts_language(language)
        ts_language_obj = tree_sitter.Language(ts_lang)
        parser = tree_sitter.Parser(ts_language_obj)
        source_bytes = file_path.read_bytes()
        tree = parser.parse(source_bytes)
    except Exception:
        return nodes, edges

    module_path = ts_module_from_path(rel_path)

    # Definitions: capture every function/method def name as a symbol node.
    def_query_str = queries.get("exports", "")
    definitions: list[str] = []
    if def_query_str:
        try:
            dq = tree_sitter.Query(ts_language_obj, def_query_str)
            dc = tree_sitter.QueryCursor(dq)
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
        cq = tree_sitter.Query(ts_language_obj, queries["calls"])
        cc = tree_sitter.QueryCursor(cq)
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
