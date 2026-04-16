"""Tree-sitter file parsing helpers.

Factored out of ``weld.strategies.tree_sitter`` so the strategy module
stays within the 400-line default cap. Handles dynamic grammar loading
and symbol extraction from a single source file.
"""

from __future__ import annotations

from pathlib import Path

_GRAMMAR_MODULE_ALIASES: dict[str, str] = {
    "csharp": "tree_sitter_c_sharp",
}
_GRAMMAR_PACKAGE_ALIASES: dict[str, str] = {
    "csharp": "tree-sitter-c-sharp",
}


def grammar_module_name(language: str) -> str:
    """Return the importable grammar module name for *language*."""
    return _GRAMMAR_MODULE_ALIASES.get(language, f"tree_sitter_{language}")


def grammar_package_name(language: str) -> str:
    """Return the pip grammar package name for *language*."""
    return _GRAMMAR_PACKAGE_ALIASES.get(
        language,
        grammar_module_name(language).replace("_", "-"),
    )


def load_ts_language(language: str) -> object:
    """Dynamically import and return the tree-sitter Language for *language*.

    The grammar packages follow the naming convention
    ``tree_sitter_{language}``.
    """
    import importlib

    module_name = grammar_module_name(language)
    try:
        mod = importlib.import_module(module_name)
    except ImportError as exc:
        raise ImportError(
            f"tree-sitter grammar for '{language}' not installed: "
            f"pip install {grammar_package_name(language)}"
        ) from exc

    # Modern tree-sitter-python grammars expose a language() function
    if hasattr(mod, "language"):
        return mod.language()
    raise ImportError(
        f"tree-sitter grammar module '{module_name}' does not expose "
        f"a language() function"
    )


def parse_file_symbols(
    file_path: Path,
    language: str,
    queries: dict[str, str],
    *,
    _language_loader: object | None = None,
) -> dict[str, list[str]]:
    """Parse a source file with tree-sitter and return extracted symbols.

    Args:
        file_path: Absolute path to the source file.
        language: Language name (must match a grammar package).
        queries: Dict of query name -> S-expression string.
        _language_loader: Override for :func:`load_ts_language` (testing).

    Returns:
        Dict mapping query name to list of captured symbol names.
    """
    import tree_sitter  # noqa: F811

    loader = _language_loader or load_ts_language
    ts_lang = loader(language)
    ts_language_obj = tree_sitter.Language(ts_lang)
    parser = tree_sitter.Parser(ts_language_obj)

    source_bytes = file_path.read_bytes()
    tree = parser.parse(source_bytes)
    result: dict[str, list[str]] = {}

    for qname, qstr in queries.items():
        names: list[str] = []
        try:
            query = tree_sitter.Query(ts_language_obj, qstr)
            cursor = tree_sitter.QueryCursor(query)
            for _pattern_idx, capture_dict in cursor.matches(tree.root_node):
                for node in capture_dict.get("name", []):
                    names.append(node.text.decode("utf-8"))
        except Exception:
            # If a query fails at runtime, skip it gracefully
            pass
        result[qname] = names

    return result
