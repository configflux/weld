"""Props a tree-sitter file node carries, derived from its parsed symbols.

Three stamps live here. ``type_uses`` (ADR 0061) is the oldest, moved from
:mod:`weld.strategies._ts_parse` when this module was minted. The re-export
helpers are gap G5's (ADR 0142 D5, bd lrnx1.5). The CommonJS helpers are gap
G6's (ADR 0142 D6, bd lrnx1.6) and are the same idea one language over: a
module publishes names it did not define, and ``props.exports`` is the wrong
place to say so.

Why a re-export needs its own vocabulary
----------------------------------------
``export { formatPrice } from "./money"`` is two facts at once, and the graph
has to keep them apart.

* The module **exports** the name -- that is its published surface, and a
  reader arriving through the package entry point is entitled to see it.
* The module does **not define** it. ``props.exports`` is what
  :func:`weld.strategies._ts_definitions.promote_definition_symbols` promotes
  into ``symbol:`` nodes with ``confidence: definite`` and ``roles:
  [implementation]``, so folding re-exports into it would mint a second
  definite ``formatPrice`` in the barrel, three lines from the real one, and
  leave every call resolution to choose between them.

So the names land on their own key, ``props.reexports``, and the *module* they
came from joins ``props.imports_from`` -- which is the half that closes the
gap: ``weld.graph_closure._link_imports`` already resolves a relative
specifier to the file that defines it, so the barrel gains a ``depends_on``
edge to ``money.ts`` with no closure change at all. A barrel is the entry
point a package's ``main`` names, and before this it was a dead end: its only
outbound edge was ``contains`` to its own ``<file>`` sentinel.

``export * from "./money"`` contributes a source and no names, and that is the
common barrel form rather than an edge case -- which is why
:func:`has_reexport_evidence` asks about either half, and why the strategy
mints a file node for a file whose only content is forwarding.

Why CommonJS exports need the same treatment
--------------------------------------------
``module.exports = { router, renderOrder }`` publishes two names, and only one
of them is defined in the file -- ``router`` is an ``express.Router()`` the
module imported the constructor for. Folding both into ``props.exports`` would
mint a definite ``symbol:javascript:...:router`` claiming this file defines the
express router type, which is the duplicate-definition problem re-exports have,
so the names land on ``props.commonjs_exports`` instead. That keeps the
published surface of a CommonJS module answerable without lying about where
its names come from.

A file whose only content is ``module.exports = require("./impl")`` is the
CommonJS twin of a barrel: it declares nothing and is still the entry point
``main`` names. :func:`has_publication_evidence` is what keeps it in the graph.
"""

from __future__ import annotations

#: Query bucket holding names re-exported from another module -- the
#: ``export { A, B } from "./m"`` and ``export type { T } from "./m"`` forms.
#: A local ``export { a }`` with no ``from`` is deliberately not in it: that
#: name *is* defined here, and the query requires the ``source:`` field.
REEXPORTS_QUERY = "reexports"

#: Query bucket holding the module specifiers of re-export statements, star
#: forms included.
REEXPORT_SOURCES_QUERY = "reexport_sources"

#: Query bucket holding the names a CommonJS module publishes -- the
#: ``module.exports = { a, b }``, ``module.exports = name``, ``exports.x`` and
#: ``module.exports.x`` forms. Declared by ``weld/languages/javascript.yaml``
#: only; every other language file omits it and the helpers below then answer
#: "nothing published", which is the truthful answer for a language with no
#: CommonJS.
COMMONJS_EXPORTS_QUERY = "commonjs_exports"


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


def reexported_names(symbols: dict[str, list[str]]) -> list[str]:
    """Names this file re-exports from another module, deduped in order."""
    return _dedupe(symbols.get(REEXPORTS_QUERY, []))


def reexport_sources(symbols: dict[str, list[str]]) -> list[str]:
    """Module specifiers this file re-exports from, deduped in order.

    Deduped because one barrel routinely forwards from the same module
    twice -- a value export and a ``export type { ... }`` beside it -- and
    the specifier is dependency evidence, which does not get truer for
    being recorded twice.
    """
    return _dedupe(symbols.get(REEXPORT_SOURCES_QUERY, []))


def has_reexport_evidence(symbols: dict[str, list[str]]) -> bool:
    """Whether *symbols* shows this file forwarding anything at all.

    Either half counts. ``export * from "./m"`` yields a source and no
    names; a file that re-exports nothing yields neither.
    """
    return bool(reexported_names(symbols) or reexport_sources(symbols))


def commonjs_exported_names(symbols: dict[str, list[str]]) -> list[str]:
    """Names this file publishes through CommonJS, deduped in order.

    Deduped because the four captured forms overlap by design: a module that
    writes ``exports.x = ...`` and later ``module.exports = { x }`` publishes
    one name twice, and a published name does not get truer for being written
    two ways.
    """
    return _dedupe(symbols.get(COMMONJS_EXPORTS_QUERY, []))


def has_publication_evidence(symbols: dict[str, list[str]]) -> bool:
    """Whether *symbols* shows this file publishing anything it did not define.

    Either mechanism counts: an ESM re-export or a CommonJS ``module.exports``.
    It is what lets the strategy keep a file node for a module whose whole
    content is forwarding -- an ESM barrel, or its CommonJS twin.
    """
    return bool(has_reexport_evidence(symbols) or commonjs_exported_names(symbols))


def stamp_publication_evidence(
    node_props: dict, symbols: dict[str, list[str]],
) -> None:
    """Stamp ``props.reexports`` / ``props.commonjs_exports``, when non-empty.

    Both keys are sorted, for the byte-identical output every discover path
    owes (ADR 0012), and absent rather than empty when there is nothing to say
    -- the same presence-check convention as ``type_uses`` above. They stay
    two keys rather than one merged "published" list because the mechanisms
    answer different questions: a re-export names a *module* the reader should
    follow (its specifier is on ``imports_from``), while a CommonJS export
    names only itself.
    """
    names = reexported_names(symbols)
    if names:
        node_props["reexports"] = sorted(names)
    commonjs = commonjs_exported_names(symbols)
    if commonjs:
        node_props["commonjs_exports"] = sorted(commonjs)


def merge_reexport_sources(symbols: dict[str, list[str]]) -> list[str]:
    """Return ``symbols["imports"]`` extended with the re-export specifiers.

    Order-preserving -- the ``import`` statements keep the order the grammar
    captured them in and the re-export sources follow -- and written back
    onto *symbols* as well as returned, so the per-import enricher
    (:func:`weld.strategies._typescript_tree_sitter.enrich_file_node`), which
    reads that key itself, classifies a re-export from a first-party
    workspace package by exactly the rules an ``import`` of it would get.
    """
    merged = list(symbols.get("imports", []))
    seen = set(merged)
    for source in reexport_sources(symbols):
        if source not in seen:
            seen.add(source)
            merged.append(source)
    symbols["imports"] = merged
    return merged


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
