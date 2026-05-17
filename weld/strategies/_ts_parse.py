"""Tree-sitter file parsing helpers.

Factored out of ``weld.strategies.tree_sitter`` so the strategy module
stays within the 400-line default cap. Handles dynamic grammar loading
and symbol extraction from a single source file.

This module also owns the per-discover :class:`ParseCache`, attached to
the strategy ``context`` dict. The cache memoises the per-language
:class:`tree_sitter.Language` and :class:`tree_sitter.Parser`, plus the
per-file (decoded source bytes, parsed tree) tuple keyed by
``(abs_path, mtime, language)``. The cache lives only for the duration
of a single ``wd discover`` invocation (no module-level globals) so
test isolation and ADR 0064 criterion 4 determinism are preserved.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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

    Uses an actual ``importlib.import_module`` attempt rather than
    ``find_spec`` so the probe agrees with what :func:`load_ts_language`
    will actually see at parse time. In Bazel's hermetic runfiles
    environment ``find_spec`` may return ``None`` for modules that are
    nevertheless importable (or vice-versa); ``import_module`` mirrors
    the production import semantics exactly.

    Kept as a public helper for callers that want a cheap up-front
    check, but the strategy itself now detects missing grammars lazily
    via the :class:`ImportError` raised by :func:`load_ts_language` on
    the first parsed file, so all environments converge on the same
    behaviour.
    """
    module_name = grammar_module_name(language)
    try:
        importlib.import_module(module_name)
        return True
    except (ImportError, ModuleNotFoundError):
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


def stamp_type_uses(node_props: dict, symbols: dict[str, list[str]]) -> None:
    """Stamp ADR 0061 ``type_uses`` prop on a file node.

    The captured USE-site type identifiers (parameter, return, friend,
    base-class, template-arg positions) are sorted + deduplicated for
    stable graph output. The prop is omitted entirely when the parser
    returned no captures so ``"type_uses" in node_props`` remains a
    meaningful presence check (matches the convention used by
    ``types``, ``imports_from``, and ``symbol_records``).
    """
    type_uses = symbols.get("type_uses", [])
    if type_uses:
        node_props["type_uses"] = sorted(set(type_uses))


@dataclass(frozen=True)
class ParseEntry:
    """Cached per-file parse output.

    Frozen so downstream consumers (e.g. ``extract_call_edges``) cannot
    accidentally swap fields and break the invariant that ``tree`` was
    parsed from ``source_bytes`` using ``parser`` configured with
    ``language_obj``.

    Attributes:
        tree: The parsed ``tree_sitter.Tree`` (typed as ``Any`` so this
            module remains importable when ``tree_sitter`` is missing).
        source_bytes: The raw bytes that were fed to ``parser.parse``.
        language_obj: The per-language ``tree_sitter.Language`` used to
            build the tree.
        parser: The per-language ``tree_sitter.Parser`` used to build
            the tree (kept so downstream consumers do not reconstruct).
    """

    tree: Any
    source_bytes: bytes
    language_obj: Any
    parser: Any


class ParseCache:
    """Per-discover memo for grammar loads, per-file parse output, and queries.

    The cache has three memo tables:

    * ``_langs[language]`` -> ``(language_obj, parser, ts_lang_capsule)``
      so the grammar is loaded and the ``Parser`` constructed exactly
      once per language per cache.
    * ``_parses[(abs_path_str, mtime_ns, language)]`` -> :class:`ParseEntry`
      so a second call for the same unchanged file is a hit.
    * ``_queries[(language, query_name)]`` -> ``tree_sitter.Query``
      so the compiled S-expression is built exactly once per
      ``(language, query_name)`` across the entire discover run rather
      than once per file. ``tree_sitter.Query(...)`` is a C-level call
      that costs ~9ms per construction in production grammars; on a
      254-file C# corpus with 12 distinct queries the savings are
      dominant (4826 reconstructions collapse to 12).

    All three tables use insertion-ordered ``dict`` so iteration is
    deterministic. No caller iterates today; the contract is documented
    here so future consumers cannot leak set-iteration order through
    the cache.
    """

    def __init__(self) -> None:
        self._langs: dict[str, tuple[Any, Any, Any]] = {}
        self._parses: dict[tuple[str, int, str], ParseEntry] = {}
        self._queries: dict[tuple[str, str], Any] = {}

    def get_or_load_language(
        self, language: str, loader: Any, tree_sitter_mod: Any,
    ) -> tuple[Any, Any]:
        """Return ``(language_obj, parser)`` for *language*.

        Loads the grammar via *loader* on first use only; subsequent
        calls for the same *language* return the memoized objects.
        """
        cached = self._langs.get(language)
        if cached is not None:
            return cached[0], cached[1]
        ts_lang = loader(language)
        language_obj = tree_sitter_mod.Language(ts_lang)
        parser = tree_sitter_mod.Parser(language_obj)
        self._langs[language] = (language_obj, parser, ts_lang)
        return language_obj, parser

    def get_parse(self, file_path: Path, language: str) -> ParseEntry | None:
        """Return the cached :class:`ParseEntry` for *file_path*, or None.

        The mtime is read fresh on every call so a file modified during
        the same discover is invalidated correctly. Returns ``None``
        when the file is absent or has not been parsed yet.
        """
        try:
            mtime_ns = file_path.stat().st_mtime_ns
        except OSError:
            return None
        key = (str(file_path), mtime_ns, language)
        return self._parses.get(key)

    def store_parse(
        self, file_path: Path, language: str, entry: ParseEntry,
    ) -> None:
        """Insert *entry* under the canonical ``(path, mtime, lang)`` key."""
        try:
            mtime_ns = file_path.stat().st_mtime_ns
        except OSError:
            return
        self._parses[(str(file_path), mtime_ns, language)] = entry

    def get_or_compile_query(
        self,
        language: str,
        query_name: str,
        query_source: str,
        language_obj: Any,
        tree_sitter_mod: Any,
    ) -> Any:
        """Return the compiled ``tree_sitter.Query`` for *(language, query_name)*.

        Memoises the C-level ``tree_sitter.Query(language_obj, query_source)``
        construction -- the dominant per-file cost on the eShopOnWeb
        C# cProfile baseline post-b1uz. The key is ``(language,
        query_name)``; the query source string is passed through only on
        first construction. Callers MUST pass the same ``query_source``
        for a given ``(language, query_name)`` within one discover
        run (the strategy reads queries from ``weld/languages/*.yaml``
        once at extract-time, so the contract holds in practice).

        Raises whatever ``tree_sitter.Query`` raises on a malformed
        query string; the caller decides whether to swallow.
        """
        key = (language, query_name)
        cached = self._queries.get(key)
        if cached is not None:
            return cached
        compiled = tree_sitter_mod.Query(language_obj, query_source)
        self._queries[key] = compiled
        return compiled


_PARSE_CACHE_KEY = "_tree_sitter_parse_cache"


def get_parse_cache(context: dict) -> ParseCache:
    """Return the :class:`ParseCache` for this discover *context*.

    Idempotent: every source in the same discover share one cache
    instance; each new ``context`` gets a fresh one. Module-level
    globals are deliberately avoided so test isolation holds.
    """
    cache = context.get(_PARSE_CACHE_KEY)
    if cache is None:
        cache = ParseCache()
        context[_PARSE_CACHE_KEY] = cache
    return cache


def parse_file_symbols(
    file_path: Path,
    language: str,
    queries: dict[str, str],
    *,
    _language_loader: object | None = None,
    cache: ParseCache | None = None,
) -> dict[str, list[str]]:
    """Parse a source file with tree-sitter and return extracted symbols.

    Args:
        file_path: Absolute path to the source file.
        language: Language name (must match a grammar package).
        queries: Dict of query name -> S-expression string.
        _language_loader: Override for :func:`load_ts_language` (testing).
        cache: Optional :class:`ParseCache` to memoise grammar loads and
            per-file parses across one ``wd discover`` invocation. When
            omitted the function falls back to its original
            no-cache behaviour (one grammar load + one parse per call).

    Returns:
        Dict mapping query name to list of captured symbol names.
    """
    import tree_sitter  # noqa: F811

    loader = _language_loader or load_ts_language

    if cache is not None:
        ts_language_obj, parser = cache.get_or_load_language(
            language, loader, tree_sitter,
        )
        entry = cache.get_parse(file_path, language)
        if entry is None:
            source_bytes = file_path.read_bytes()
            tree = parser.parse(source_bytes)
            entry = ParseEntry(
                tree=tree,
                source_bytes=source_bytes,
                language_obj=ts_language_obj,
                parser=parser,
            )
            cache.store_parse(file_path, language, entry)
        tree = entry.tree
    else:
        ts_lang = loader(language)
        ts_language_obj = tree_sitter.Language(ts_lang)
        parser = tree_sitter.Parser(ts_language_obj)
        source_bytes = file_path.read_bytes()
        tree = parser.parse(source_bytes)

    result: dict[str, list[str]] = {}
    for qname, qstr in queries.items():
        names: list[str] = []
        try:
            if cache is not None:
                query = cache.get_or_compile_query(
                    language, qname, qstr, ts_language_obj, tree_sitter,
                )
            else:
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
