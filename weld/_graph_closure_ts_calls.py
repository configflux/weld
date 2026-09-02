"""Bind a TypeScript call to the definition its import names (ADR 0142 D2).

The tree-sitter call-graph pass sees one file at a time, so a callee it cannot
find in that file is all it can say: ``symbol:unresolved:formatPrice``. On a
Node workspace that is *every* interesting call -- the shared function, the
workspace package, the aliased module -- and ``wd callers`` answered nothing
for TypeScript at all (gap G2, bd lrnx1.3). The missing half is the merged
graph, which is what this pass has.

Each hinted edge arrives carrying the import that introduced its callee's name
(:mod:`weld.strategies._ts_call_sites`): the local spelling, the name the
defining module exports it under, the specifier, and -- when the first-party
index could bind it -- the file that specifier names. Two rules then read that
against the graph, most specific first:

1. **The bound file defines it.** ``@/lib/greeting`` binds to
   ``apps/web/src/lib/greeting.ts``, which holds a definite ``greeting``. This
   is a proof, not an inference: the import names the module and the module
   names the symbol.

2. **The bound file defines nothing, and its own directory defines it once.**
   A workspace package's ``main`` is usually a barrel -- ``export { formatPrice
   } from "./money"`` -- which contributes no symbol of its own, so rule 1 has
   nothing to land on and the re-export chain a closure could walk does not
   exist yet (gap G5). The package directory is then the smallest container the
   graph can prove the name was imported from, and *exactly one* definition of
   it inside that directory is what makes the reading a fact rather than a
   guess. Two would be an ambiguity this pass has no evidence to break.

Everything else keeps the sentinel, which is the honest answer under ADR 0134:
a third-party specifier (``express``), a member call on a value
(``Response.json``), a name the bound package does not define, and an
ambiguous one. This pass never mints a target -- it only ever names a definite
symbol the graph already holds -- so a resolution here cannot invent an id.

Like its Python siblings it **undoes itself first**. The endpoint it moves sits
on a *retained* edge, and an incremental round does not re-walk a clean caller,
so a move made in an earlier round would otherwise be inherited no matter what
happened to the defining file since. Restoring every hinted endpoint to its
sentinel and re-deriving makes the result a function of the hint plus the
current graph, identical on the full and the incremental path.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from weld.strategies._ts_call_sites import (
    CALL_SITE_LANGUAGES,
    UNRESOLVED_PREFIX,
    TsImportHint,
    read_ts_import_hint,
    unresolved_sentinel_node,
)

#: Resolution tag stamped on an edge this pass bound. Shared with the Python
#: closure's import-attribute rule on purpose: both answer "the caller imported
#: this name", and one vocabulary is what lets a reader filter call evidence by
#: how it was resolved without learning a per-language dialect.
_RESOLUTION = "import"

#: Extensions a bare relative specifier may name, in TypeScript's own
#: resolution order. ``.d.ts`` is not listed separately -- a declaration file
#: is reached through the ``.ts`` entry above it or not at all -- and a
#: specifier that already carries an extension is tried literally first.
_MODULE_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")

#: Language tag used when a caller node records none. Only reachable for a
#: graph written by a strategy that stamped no language on its call-site
#: symbol; the sentinel origin rule (ADR 0042) needs *some* language to
#: classify against, and every writer of this hint is a TypeScript one.
_DEFAULT_LANGUAGE = "typescript"


class _Definitions:
    """The definite TypeScript symbols this graph holds, indexed two ways.

    ``by_file`` answers rule 1 and ``by_name`` answers rule 2; ``defining``
    is the guard between them -- the set of files that contribute at least one
    definition, so "this entry file is opaque to the extractor" is a fact read
    off the graph rather than a property of the path.
    """

    __slots__ = ("by_file", "by_name", "defining")

    def __init__(self, nodes: dict[str, dict]) -> None:
        self.by_file: dict[tuple[str, str], str] = {}
        self.by_name: dict[str, list[tuple[str, str]]] = {}
        self.defining: set[str] = set()
        for node_id, node in sorted(nodes.items()):
            record = _definition_record(node)
            if record is None:
                continue
            rel_path, name = record
            self.by_file.setdefault((rel_path, name), node_id)
            self.by_name.setdefault(name, []).append((rel_path, node_id))
            self.defining.add(rel_path)


def _definition_record(node: dict) -> tuple[str, str] | None:
    """``(file, name)`` when *node* is a definite TypeScript definition.

    ``<file>`` is excluded by name: it is the synthetic module-level caller the
    call-graph pass mints for every parsed file, not a definition, and letting
    it in would make every file "defining" and switch rule 2 off everywhere.
    """
    if node.get("type") != "symbol":
        return None
    props = node.get("props")
    if not isinstance(props, dict) or props.get("confidence") != "definite":
        return None
    if props.get("language") not in CALL_SITE_LANGUAGES:
        return None
    rel_path, name = props.get("file"), props.get("qualname")
    if not (isinstance(rel_path, str) and rel_path):
        return None
    if not (isinstance(name, str) and name) or name == "<file>":
        return None
    return rel_path, name


def resolve_ts_call_targets(
    nodes: dict[str, dict], edges: list[dict], path_index: dict[str, str],
) -> None:
    """Re-derive every hinted TypeScript ``calls`` endpoint against the graph."""
    hinted = [
        (edge, hint)
        for edge in edges
        if edge.get("type") == "calls"
        for hint in (read_ts_import_hint(edge.get("props")),)
        if hint is not None
    ]
    if not hinted:
        return
    sentinels = _restore_sentinels(nodes, hinted)
    definitions = _Definitions(nodes)
    moved = False
    for edge, hint in hinted:
        target = _resolve(hint, _caller_file(nodes, edge), definitions, path_index)
        if target is None:
            continue
        edge["to"] = target
        props = edge["props"]
        props["resolved"] = True
        props["confidence"] = "definite"
        props["resolution"] = _RESOLUTION
        moved = True
    if moved:
        _drop_unreferenced(nodes, edges, sentinels)


def _restore_sentinels(
    nodes: dict[str, dict], hinted: list[tuple[dict, TsImportHint]],
) -> set[str]:
    """Put every hinted endpoint back on its sentinel; report which ones.

    The sentinel node is re-minted rather than assumed present: a previous
    round may have resolved every edge that named it, in which case
    :func:`_drop_unreferenced` removed it. Restoring the endpoint without the
    node would leave a dangling edge for the post-pass to prune, and the
    honest "cannot answer" record would vanish with it.
    """
    sentinels: set[str] = set()
    for edge, hint in hinted:
        sentinel = f"{UNRESOLVED_PREFIX}{hint.local}"
        sentinels.add(sentinel)
        edge["to"] = sentinel
        props = edge["props"]
        props["resolved"] = False
        props["confidence"] = "speculative"
        props["resolution"] = "unresolved"
        nodes.setdefault(
            sentinel,
            unresolved_sentinel_node(
                hint.local, _edge_language(nodes, edge),
            ),
        )
    return sentinels


def _resolve(
    hint: TsImportHint,
    caller_file: str,
    definitions: _Definitions,
    path_index: dict[str, str],
) -> str | None:
    """The definition *hint* names, or ``None`` when the graph cannot say."""
    bound = hint.target or _relative_target(hint.specifier, caller_file, path_index)
    if not bound or bound not in path_index:
        # The module the import names is not in this graph, so nothing here can
        # say whether it defines the name. Rule 2 below would still find a
        # candidate beside it -- and would be wrong exactly when the module the
        # import *did* name defines it too, which is the reading rule 1 owns.
        return None
    exact = definitions.by_file.get((bound, hint.name))
    if exact is not None:
        return exact
    if bound in definitions.defining:
        # The entry file was read and does not export this name. Reaching past
        # it would be guessing at a module the import did not name.
        return None
    return _sole_definition_under(PurePosixPath(bound).parent.as_posix(), hint, definitions)


def _sole_definition_under(
    directory: str, hint: TsImportHint, definitions: _Definitions,
) -> str | None:
    """The one definition of ``hint.name`` under *directory*, or ``None``.

    *directory* is ``"."`` for an entry at the repository root, which reads as
    "the package is the whole repository" -- correct for a single-package repo,
    and still subject to the same uniqueness requirement, so it cannot resolve
    a name the repo declares twice.
    """
    prefix = "" if directory in ("", ".") else f"{directory}/"
    hits = [
        node_id
        for rel_path, node_id in definitions.by_name.get(hint.name, ())
        if rel_path.startswith(prefix)
    ]
    return hits[0] if len(hits) == 1 else None


def _relative_target(
    specifier: str, caller_file: str, path_index: dict[str, str],
) -> str:
    """The file a ``./`` or ``../`` specifier names, or ``""``.

    Resolved here rather than in the strategy because the graph already knows
    which files exist: the path index is the same evidence a filesystem walk
    would produce, and asking it costs nothing. Only bare and index-suffixed
    readings are tried -- an extension-rewriting specifier (``./money.js``
    meaning ``money.ts`` under ``NodeNext``) is left to the literal reading and
    otherwise declines, which keeps the rule to shapes the path index can
    prove.
    """
    if not specifier.startswith(("./", "../")) or not caller_file:
        return ""
    base = PurePosixPath(caller_file).parent
    rel = _clean(base.joinpath(specifier).as_posix())
    if not rel:
        return ""
    candidates = [rel]
    if not PurePosixPath(rel).suffix:
        candidates.extend(f"{rel}{ext}" for ext in _MODULE_EXTS)
        candidates.extend(f"{rel}/index{ext}" for ext in _MODULE_EXTS)
    for candidate in candidates:
        if candidate in path_index:
            return candidate
    return ""


def _clean(value: str) -> str:
    """*value* with ``.`` and ``..`` segments applied, as a repo-relative path."""
    parts: list[str] = []
    for part in PurePosixPath(value).parts:
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _caller_file(nodes: dict[str, dict], edge: dict) -> str:
    """The repo-relative file the edge's caller lives in, or ``""``."""
    node = nodes.get(str(edge.get("from") or ""))
    props = node.get("props") if isinstance(node, dict) else None
    value = props.get("file") if isinstance(props, dict) else None
    return value if isinstance(value, str) else ""


def _edge_language(nodes: dict[str, dict], edge: dict) -> str:
    """The language of the edge's caller, for the sentinel's origin rule."""
    node = nodes.get(str(edge.get("from") or ""))
    props = node.get("props") if isinstance(node, dict) else None
    value = props.get("language") if isinstance(props, dict) else None
    if isinstance(value, str) and value in CALL_SITE_LANGUAGES:
        return value
    return _DEFAULT_LANGUAGE


def _drop_unreferenced(
    nodes: dict[str, dict], edges: list[dict], sentinels: set[str],
) -> None:
    """Drop each restored sentinel no edge names any more.

    Counted rather than popped, for the reason the Python import-attribute
    pass counts: the sentinel id is a bare-name namespace shared by every
    strategy that failed to resolve the same name, so another call site's
    ``formatPrice()`` may still be unresolved. Both endpoints count -- what
    makes a node worth keeping is that something still points at it, not
    which direction.
    """
    referenced = {edge.get(side) for edge in edges for side in ("from", "to")}
    for sentinel in sentinels:
        if sentinel not in referenced:
            nodes.pop(sentinel, None)


__all__ = ["resolve_ts_call_targets"]
