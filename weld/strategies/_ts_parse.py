"""Tree-sitter file parsing helpers.

Factored out of ``weld.strategies.tree_sitter`` so the strategy module
stays within the 400-line default cap. Handles dynamic grammar loading
and symbol extraction from a single source file.
"""

from __future__ import annotations

import importlib.util as _importlib_util
from pathlib import Path

_GRAMMAR_MODULE_ALIASES: dict[str, str] = {
    "csharp": "tree_sitter_c_sharp",
}
_GRAMMAR_PACKAGE_ALIASES: dict[str, str] = {
    "csharp": "tree-sitter-c-sharp",
}
# Some grammars bundle multiple languages under one package and use
# language-specific function names rather than the generic language().
# Map the weld language key to the function name to call.
_GRAMMAR_LANGUAGE_FN: dict[str, str] = {
    "typescript": "language_typescript",
    "tsx": "language_tsx",
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


def grammar_available(language: str) -> bool:
    """Probe whether the per-language tree-sitter grammar is importable.

    Returns ``True`` when ``importlib.util.find_spec(grammar_module)``
    yields a non-``None`` spec. A missing grammar manifests as an
    ``ImportError`` deep inside :func:`load_ts_language`, which today
    is caught silently per-file -- so callers must probe BEFORE the
    file loop to avoid the silent-no-op failure mode.
    """
    module_name = grammar_module_name(language)
    try:
        return _importlib_util.find_spec(module_name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


# Context-state key for per-discovery-run dedup of grammar-missing
# warnings. Multiple source entries can target the same language; we
# only want one explicit WARN per language per run.
_MISSING_GRAMMAR_KEY = "_tree_sitter_missing_grammars"


def append_missing_grammar_warning(language: str, context: dict) -> None:
    """Append one structured warning naming a missing grammar, dedup-aware.

    Stores seen languages under ``context[_MISSING_GRAMMAR_KEY]`` so
    repeated source entries for the same language emit only one
    warning. The message follows the existing install-hint pattern so
    discovery's stderr drain produces a single explicit line per
    language with the canonical ``pip install`` command.
    """
    seen: set = context.setdefault(_MISSING_GRAMMAR_KEY, set())
    if language in seen:
        return
    seen.add(language)
    pkg = grammar_package_name(language)
    context.setdefault("_warnings", []).append(
        f"tree-sitter grammar for '{language}' not installed; "
        f"skipping tree_sitter strategy for {language} sources. "
        f"Install with: pip install {pkg}"
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

    # Some grammars (e.g. tree_sitter_typescript) use a language-specific
    # function name rather than the generic language().
    lang_fn = _GRAMMAR_LANGUAGE_FN.get(language)
    if lang_fn and hasattr(mod, lang_fn):
        return getattr(mod, lang_fn)()
    # Modern tree-sitter grammars expose a generic language() function
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
