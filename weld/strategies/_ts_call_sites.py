"""What a TypeScript call site sits inside, and where its callee name came from.

The tree-sitter call-graph pass (:mod:`weld.strategies._ts_call_graph`) reads a
``calls`` query and gets back bare identifiers: the text at the call site and
nothing else. That is enough to record *that* a call happened and no more, so
every TypeScript ``calls`` edge historically ran from a synthetic ``<file>``
symbol to ``symbol:unresolved:<name>`` -- a caller that is a whole file and a
callee nothing ever resolved, which is why ``wd callers`` could not answer for
TypeScript at all (gap G2, ADR 0142 D2, bd lrnx1.3).

Two facts the grammar *does* hold turn that into an answerable edge, and this
module reads both:

* **What the call site is written inside.** :func:`enclosing_definition` walks
  up from the captured identifier to the nearest ancestor whose ``name`` field
  names a definition the same file exports. That is deliberately narrower than
  "the nearest enclosing function": the pass may only attribute a call to a
  symbol node it actually minted, and it mints one per *exported* definition.
  A call inside a non-exported helper, or inside an anonymous callback at
  module level, therefore keeps the file sentinel -- which is a true statement
  about the file rather than a fabricated one about a symbol that is not in
  the graph.

* **Where the callee's name was imported from.** :func:`import_bindings` reads
  the file's named imports as ``{local name: (exported name, specifier)}``, so
  a callee that is an imported binding can carry the specifier it came from.

The binding is recorded on the *edge* as :data:`TS_IMPORT_PROP`, not on the
importing file's node, and that is not a style choice. A later source entry may
legitimately claim the same ``file:`` id and win the merge -- the express
strategy's boundary placeholder does exactly that to a ``.ts`` file it finds
routes in -- and the file node's import props go with it. Edge props survive,
so the evidence stays attached to the thing that needs it.

Only *named* imports are read. A default import binds a local name that is not
an exported symbol name, and a namespace import is called through a member
expression whose captured identifier is the member, not the binding -- neither
could resolve, so recording a hint for them would add a prop with no reader.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from weld.strategies._language_origin import origin_for_callgraph_sentinel

#: The languages whose call sites carry the attribution and import evidence
#: this module derives. Every other language keeps the file-sentinel caller and
#: the bare sentinel callee: their grammars are read by the same call-graph
#: pass, and widening the behaviour to them without a corpus that measures it
#: is how a shared pass acquires per-language regressions nobody asked for.
CALL_SITE_LANGUAGES: frozenset[str] = frozenset(
    {"typescript", "tsx", "javascript", "jsx"}
)

#: The id prefix a call-graph strategy mints for a callee it could not bind.
UNRESOLVED_PREFIX = "symbol:unresolved:"

#: Edge-prop key carrying the import a callee name was bound by. Read by
#: :mod:`weld._graph_closure_ts_calls`, which re-derives the endpoint from it
#: on every closure run.
TS_IMPORT_PROP = "ts_import"

#: The query that yields the named-import table. Absent from a language file
#: means "this language has no import evidence", not an error.
IMPORT_BINDINGS_QUERY = "import_bindings"


class TsImportHint(NamedTuple):
    """The import that introduced a callee's name into the calling file.

    ``local`` is the name the call was written under and is what the sentinel
    is keyed on, so a restore needs nothing but this record. ``name`` is the
    name the *defining* module exports it as -- the two differ under
    ``import { formatPrice as fp }``, and looking the definition up under the
    local spelling would find nothing. ``target`` is the repo-relative file
    the specifier binds to when the first-party index (ADR 0142 D3) knew one,
    and ``""`` otherwise -- a third-party specifier, or a relative one the
    closure resolves for itself against the path index.
    """

    local: str
    name: str
    specifier: str
    target: str


def read_ts_import_hint(props: Any) -> TsImportHint | None:
    """The hint *props* carries, or ``None``.

    Defensive about shape because a graph on disk may have been written by any
    version: every field is required to be a non-empty string except
    ``target``, which is legitimately empty whenever the specifier bound to no
    file the strategy could name.
    """
    if not isinstance(props, dict):
        return None
    raw = props.get(TS_IMPORT_PROP)
    if not isinstance(raw, dict):
        return None
    local, name = raw.get("local"), raw.get("name")
    specifier, target = raw.get("from"), raw.get("target", "")
    if not (isinstance(local, str) and local):
        return None
    if not (isinstance(name, str) and name):
        return None
    if not (isinstance(specifier, str) and specifier):
        return None
    return TsImportHint(
        local, name, specifier, target if isinstance(target, str) else ""
    )


def import_hint_props(local: str, name: str, specifier: str) -> dict[str, str]:
    """The hint as it is first written, before a target is known.

    ``target`` is present and empty rather than absent so the recorded shape is
    the same whether or not the binding was resolvable -- a reader never has to
    tell "no target" from "an older writer".
    """
    return {"local": local, "name": name, "from": specifier, "target": ""}


def bind_hint_target(props: Any, target: str) -> None:
    """Record *target* on the hint *props* already carries, if any."""
    if not isinstance(props, dict) or not target:
        return
    raw = props.get(TS_IMPORT_PROP)
    if isinstance(raw, dict):
        raw["target"] = target


def unresolved_sentinel_node(callee: str, language: str) -> dict:
    """The node a call-graph pass mints for a callee it could not bind.

    One spelling, two writers: the strategy mints it at extraction time and
    the closure re-mints it when it puts a hinted endpoint back (an earlier
    run may have resolved the edge and left the sentinel unreferenced, so it
    can genuinely be absent). A second, drifting derivation would make a
    re-derived graph differ from a freshly discovered one for no reason a
    reader could see.
    """
    return {
        "type": "symbol",
        "label": callee,
        "props": {
            "qualname": callee,
            "language": language,
            # ADR 0064 criterion 2: every symbol carries a ``kind``.
            # Unresolved call-site sentinels are synthetic weld modelling --
            # they may later be rewritten by layer-2 resolvers (C++ headers),
            # the C# inheritance pass, or the TypeScript import closure.
            # ``"unresolved"`` is listed in ``tier_check_kinds._SYNTHETIC_KINDS``
            # so it does not count toward the criterion-1 vocabulary tally.
            "kind": "unresolved",
            "resolved": False,
            "source_strategy": "tree_sitter",
            "authority": "derived",
            "confidence": "speculative",
            "roles": ["implementation"],
            # ADR 0042: classify the sentinel per-language. For C++ this stays
            # ``"unresolved"`` so layer-2's include resolver can upgrade it;
            # for TS/JS the JS built-in globals (``Array``, ``Math``, ...)
            # collapse to ``stdlib``; for Rust the ``std::``/``core::``/
            # ``alloc::`` qualifier does the same. Go / Java / C# default to
            # ``unresolved`` at this layer because the bare-name capture is
            # not enough signal -- Go's richer import-layer classification
            # lives in ``weld.strategies._go_origin``.
            "origin": origin_for_callgraph_sentinel(language, callee),
        },
    }


def enclosing_definition(node: Any, definitions: frozenset[str] | set[str]) -> str:
    """The nearest enclosing definition in *definitions*, or ``""``.

    Walks the ancestor chain of the captured identifier and reads each
    ancestor's ``name`` field. That one field covers every shape a call can be
    written inside without enumerating node types: a ``function_declaration``
    (``export function GET()``), a ``class_declaration`` around a method the
    ``exports`` query did not capture on its own, and -- the shape a Node
    codebase is mostly made of -- a ``variable_declarator`` holding an arrow
    function (``export const handler = () => ...``), whose arrow is anonymous
    and whose name lives one level up.

    Membership in *definitions* is the admission rule, not merely a filter:
    those are exactly the names the caller minted symbol nodes for, so an
    ancestor outside the set has no node for an edge to start at. Returning
    ``""`` leaves the call attributed to the file, which is what the graph can
    prove.

    Total by construction: a parser whose nodes do not expose ``parent`` or
    ``child_by_field_name`` (a mocked one, most often) yields ``""`` rather
    than taking the whole file's call edges down with it.
    """
    if not definitions:
        return ""
    try:
        current = getattr(node, "parent", None)
        while current is not None:
            name_node = current.child_by_field_name("name")
            if name_node is not None:
                text = name_node.text.decode("utf-8", "replace")
                if text in definitions:
                    return text
            current = current.parent
    except Exception:
        return ""
    return ""


def import_bindings(
    tree: Any,
    language: str,
    language_obj: Any,
    queries: dict[str, str],
    tree_sitter_mod: Any,
    *,
    cache: Any = None,
) -> dict[str, tuple[str, str]]:
    """``{local name: (exported name, specifier)}`` for *tree*'s named imports.

    Empty whenever the language file declares no ``import_bindings`` query, the
    query fails to compile, or the file has no named imports -- the call-graph
    pass then records no hints and every callee keeps the sentinel it always
    had, which is the behaviour this whole module is additive to.

    First binding wins on a duplicate local name. Two imports binding one name
    is not legal TypeScript, so this only fires on a broken or partially
    parsed file, and taking the first keeps the result a function of source
    order rather than of dict iteration.
    """
    query_str = queries.get(IMPORT_BINDINGS_QUERY, "")
    if not query_str or language not in CALL_SITE_LANGUAGES:
        return {}
    bindings: dict[str, tuple[str, str]] = {}
    try:
        query = _compile(
            cache, language, IMPORT_BINDINGS_QUERY, query_str,
            language_obj, tree_sitter_mod,
        )
        cursor = tree_sitter_mod.QueryCursor(query)
        for _pattern, caps in cursor.matches(tree.root_node):
            specifier = _first_specifier(caps)
            if not specifier:
                continue
            for spec_node in caps.get("specifier", []):
                local, name = _specifier_names(spec_node)
                if not local or not name:
                    continue
                bindings.setdefault(local, (name, specifier))
    except Exception:
        return {}
    return bindings


def _compile(
    cache: Any,
    language: str,
    query_name: str,
    query_str: str,
    language_obj: Any,
    tree_sitter_mod: Any,
) -> Any:
    """The compiled query, through the per-discover memo when there is one."""
    if cache is not None:
        return cache.get_or_compile_query(
            language, query_name, query_str, language_obj, tree_sitter_mod,
        )
    return tree_sitter_mod.Query(language_obj, query_str)


def _first_specifier(caps: dict) -> str:
    """The module specifier of a match, with its quote characters removed."""
    for node in caps.get("source", []):
        return strip_quotes(node.text.decode("utf-8", "replace"))
    return ""


def _specifier_names(spec_node: Any) -> tuple[str, str]:
    """``(local, exported)`` for one ``import_specifier`` node.

    ``import { formatPrice }`` binds one name to itself; ``import { formatPrice
    as fp }`` binds ``fp`` locally to the module's ``formatPrice``. The two
    fields are read rather than the node's whole text so the ``as`` form does
    not have to be re-parsed out of a string.
    """
    name_node = spec_node.child_by_field_name("name")
    if name_node is None:
        return "", ""
    exported = name_node.text.decode("utf-8", "replace")
    alias_node = spec_node.child_by_field_name("alias")
    local = (
        alias_node.text.decode("utf-8", "replace")
        if alias_node is not None
        else exported
    )
    return local, exported


def strip_quotes(raw: str) -> str:
    """Strip the surrounding quote characters from a tree-sitter capture.

    The TypeScript grammar's ``import_statement`` source rule captures the
    string node *with* its delimiters (``"react"`` not ``react``). The strip is
    conservative: only the matching first/last character is removed, and only
    when both ends agree on a quote style.
    """
    if len(raw) < 2:
        return raw
    first, last = raw[0], raw[-1]
    if first == last and first in ('"', "'", "`"):
        return raw[1:-1]
    return raw


__all__ = [
    "CALL_SITE_LANGUAGES",
    "IMPORT_BINDINGS_QUERY",
    "TS_IMPORT_PROP",
    "TsImportHint",
    "UNRESOLVED_PREFIX",
    "bind_hint_target",
    "enclosing_definition",
    "import_bindings",
    "import_hint_props",
    "read_ts_import_hint",
    "strip_quotes",
    "unresolved_sentinel_node",
]
