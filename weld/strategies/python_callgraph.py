"""Strategy: Function-level call graph extraction for Python.

Walks every Python module under a glob, records ``symbol`` nodes for each
top-level and nested ``def`` / ``async def`` / ``ClassDef``, and emits a
``calls`` edge for each call site inside a function body. ADR 0122 extends
call-site coverage beyond function bodies: a ``calls`` edge sourced at the
module's ``file:`` node for module-level statements, a ``calls`` edge
sourced at a class's own symbol for class-body statements (including a
direct def's own parameter defaults, at any enclosing scope -- module,
class, or function, per the ADR 0122 2026-08-21 amendment / bd z0fh), and a
distinct ``decorates`` edge (decorator's resolved target -> decorated
symbol) for every ``decorator_list`` entry at any nesting depth. ADR 0127
(bd lid2) adds a third, distinct edge: ``references``, for a bare-name
VALUE reference (not a call, e.g. a class passed by name as a
keyword-argument value) that resolves to a same-module top-level symbol --
sourced the same way ``calls`` is (the referencing symbol, or the
module's ``file:`` anchor for a module-level statement), but never for a
cross-module or unresolved hit (see ``_python_references.py``).

Resolution is best-effort and explicitly partial -- see ADR
``weld/docs/adr/0004-call-graph-schema-extension.md``:

1. **Same-module name lookup**: ``foo()`` resolves to a sibling
   ``def foo`` defined in the same module.
2. **Import-table lookup**: ``baz()`` resolves to ``symbol:py:foo.bar:baz``
   when the module declares ``from foo.bar import baz``. ``mod.func()``
   resolves to ``symbol:py:foo.bar:func`` when ``import foo.bar as mod``
   (or ``import foo.bar``) is in scope. An attribute call reads the table's
   attr slot to tell those two apart: only a module alias (empty slot) --
   or a from-imported name that is itself a module THIS GLOB owns -- lets
   the attribute become the symbol name. ``baz.method()`` for an imported
   *value* takes rule 3, never ``symbol:py:foo.bar:method``.

   The table's module slot is read the way the interpreter reads it, not the
   way the source spells it, and two rules correct it once -- before any of
   the three branches that read it -- rather than each branch learning them.
   ``_python_relative_import`` resolves an explicit relative import against the
   importing file's own package, so ``from .helper import work`` in
   ``pkg/caller.py`` means ``pkg.helper``; ``_python_source_root_import`` then
   handles the absolute case, binding a written name against the first
   ancestor directory that is not a package -- the one Python puts on
   ``sys.path`` -- so ``from helper import work`` in ``tools/lint.py`` means
   ``tools.helper`` and ``from acme_notify.config import load_config`` in
   ``src/acme_notify/runner.py`` means ``src.acme_notify.config``. Neither
   invents a module: each refuses and leaves the call to rule 3 when it
   cannot name a real one.
3. **Unresolved fallback**: anything else becomes
   ``symbol:unresolved:<name>``. Strategies never silently drop a call.

Rule 2's glob bound is deliberate, and it is why rule 3 is not always the
last word. A submodule another glob owns cannot be told from a value here,
so rather than answer either way on a set that a full and an incremental
discover derive differently, the resolver records what the reading turns on
(``props.import_attr``) and leaves the sentinel for
:mod:`weld._graph_closure_import_attr`, which runs once per discover over the
whole merged graph on both paths.

The strategy uses stdlib ``ast`` only -- no new mandatory dependencies.
"""

from __future__ import annotations

import ast
from pathlib import Path

from weld._rel_path import rel_to_root
from weld.strategies._glob_resolve import resolve_glob_with_provenance
from weld.strategies._helpers import StrategyResult
from weld.strategies._python_callgraph_incremental import (
    dirty_matched,
    get_incremental_hint,
    reconstruct_project_modules,
)
from weld.strategies._python_callgraph_visitor import _CallGraphVisitor
from weld.strategies._python_calls import emit_symbol_call_edges
from weld.strategies._python_decorates import emit_decorates_edges
from weld.strategies._python_inherits import emit_inherits_edges
from weld.strategies._python_lazy_api import import_alias_names
from weld.strategies._python_output_sink import mark_output_sink_callers
from weld.strategies._python_references import emit_reference_edges
from weld.strategies._python_relative_import import absolute_module, package_of
from weld.strategies._python_scope_calls import emit_module_scope_call_edges
from weld.strategies._python_source_root_import import normalize_source_root_imports
from weld.strategies._python_origin import (  # noqa: F401 -- re-export
    UNRESOLVED_PREFIX,
    is_builtin_name,
    project_module_set,
    symbol_id as _symbol_id,
    unresolved_id as _unresolved_id,
)

# ---------------------------------------------------------------------------
# ID helpers
# ---------------------------------------------------------------------------

# ``UNRESOLVED_PREFIX`` / ``_symbol_id`` / ``_unresolved_id`` are re-exported
# from ``_python_origin``, which owns the whole id shape -- the readers
# (``module_from_symbol_id``, ``qualname_from_symbol_id``), the node minters
# that stamp them, and now the two minters that build them. Historical names
# kept, so every module that imports them from here is unchanged; what moved
# is where they are DEFINED, so an emitter can mint an id without importing
# the strategy that dispatches into it.

def _module_dotted_path(rel_path: str) -> str:
    """Return a python-style dotted module path for *rel_path*.

    ``weld/strategies/python_callgraph.py`` -> ``weld.strategies.python_callgraph``
    ``services/api/app.py`` -> ``services.api.app``
    ``foo/__init__.py`` -> ``foo``
    """
    p = Path(rel_path)
    parts = list(p.parts)
    if not parts:
        return ""
    last = parts[-1]
    if last == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = Path(last).stem
    return ".".join(parts)

# ---------------------------------------------------------------------------
# Import-table extraction
# ---------------------------------------------------------------------------

def _build_import_table(
    tree: ast.Module, *, package: str = ""
) -> dict[str, tuple[str, str]]:
    """Return ``{local_name: (module, attr)}`` for every import.

    For ``from foo.bar import baz`` the entry is
    ``"baz": ("foo.bar", "baz")``.
    For ``from foo.bar import baz as qux`` the entry is
    ``"qux": ("foo.bar", "baz")``.
    For ``import foo.bar`` the entry is ``"foo": ("foo.bar", "")`` so
    that ``foo.bar.func()`` can resolve via attribute lookup.
    For ``import foo.bar as mod`` the entry is ``"mod": ("foo.bar", "")``.
    The empty-string ``attr`` slot signals "this is a module alias --
    treat the call's attribute as the symbol name".

    *package* is the importing file's own ``__package__``, and it is what makes
    an explicit relative import resolvable: ``from .helper import work`` under
    package ``pkg`` records ``("pkg.helper", "work")`` rather than the source's
    bare ``helper``, a module that exists under no spelling. A caller with no
    package position to offer leaves it empty; every relative import is then
    refused rather than guessed at, as is one whose level walks past the
    top-level package. See ``_python_relative_import`` (bd ``zr486``).

    Which local name each spelling binds is
    :func:`weld.strategies._python_lazy_api.import_alias_names`, shared with the
    accessor check that reads this table back (bd ``80zz3``) so the two cannot
    disagree about what ``import foo.bar`` puts in scope.
    """
    table: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            level = node.level or 0
            if level:
                module = absolute_module(node.module, level, package=package)
                if module is None:
                    continue
            elif node.module:
                module = node.module
            else:
                continue
            for local, alias in import_alias_names(node):
                table[local] = (module, alias.name)
        elif isinstance(node, ast.Import):
            for local, alias in import_alias_names(node):
                table[local] = (alias.name, "")
    return table

# ---------------------------------------------------------------------------
# Strategy entry point
# ---------------------------------------------------------------------------

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Walk a glob of Python files and extract symbols + ``calls`` edges."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source.get("glob", "")
    if not pattern:
        return StrategyResult(nodes, edges, discovered_from)
    excludes = source.get("exclude", [])

    matched, dirs = resolve_glob_with_provenance(root, pattern, excludes)
    discovered_from.extend(dirs)
    if not matched:
        return StrategyResult(nodes, edges, discovered_from)

    # ADR 0074: incremental dirty-scoping. When the orchestrator hands a
    # dirty-file hint, parse only the dirty subset of this glob and rebuild
    # the cross-file ``project_modules`` set from the post-purge prior graph
    # instead of re-deriving it from a full sibling parse. ``hint is None``
    # (full discover + every non-incremental caller) keeps the whole-glob
    # behaviour byte-for-byte.
    hint = get_incremental_hint(context)
    parse_files = matched
    # This glob's OWN module paths, derived identically on both discover
    # paths from the whole resolved glob (never the dirty subset). The
    # submodule-vs-value reading of ``from PARENT import CHILD`` is keyed on
    # this and not on the wider ``project_modules`` below, which the two
    # paths derive differently on purpose -- see ``_CallGraphVisitor``.
    glob_modules = project_module_set(
        root, matched, module_dotted_path=_module_dotted_path,
    )
    project_modules: frozenset[str]
    if hint is not None:
        # Decision item 4 said reconstruction is an optimization and the
        # full-glob derivation is always correct, and fell back to it only when
        # reconstruction came back empty. Taking the union unconditionally is
        # the stronger reading of the same rule, and it costs nothing: the glob
        # is already resolved, and ``project_module_set`` only maps those paths
        # to dotted names -- no file is parsed or even opened.
        #
        # The conditional fallback left one hole. Reconstruction reads
        # project membership off surviving *symbol* nodes, so a first-party
        # module that contributes no symbol at all -- a pure re-export facade,
        # a package ``__init__`` -- is missing from a set that is not empty
        # overall, and a call resolving into it is tagged ``external`` where a
        # full discover says ``project``. Deriving this glob's own membership
        # exactly the way the full path does closes that; the prior-node scan
        # stays for what it alone can supply, the union across other globs.
        parse_files = dirty_matched(matched, root, hint.dirty_files)
        project_modules = reconstruct_project_modules(
            hint.prior_nodes, parse_files, root,
            module_dotted_path=_module_dotted_path,
        ) | glob_modules
    else:
        # Project-membership set per ADR 0042 §"Per-language detection rules"
        # (Python). The "project file set" for an extract() call is the set
        # of dotted module paths derived from the matched source files.
        # Imports whose target module matches any of these paths classify
        # as ``project``; imports outside both this set and
        # ``sys.stdlib_module_names`` classify as ``external``.
        project_modules = glob_modules

    # Publish this batch's project module paths to a run-level union in
    # the shared ``context`` (ADR 0042 §Python: "any project file set
    # discovered by THIS RUN"). A multi-glob config runs one extract()
    # per glob, so a cross-glob call target resolves against a batch set
    # that does not contain it and is mislabelled ``external``. The
    # post-discovery reconciliation pass uses this union -- which is keyed
    # on the source file set, not on node survival -- to heal those tags
    # even when no batch left a surviving definite ``project`` node for the
    # module (ADR 0103 stops the stub clobbering one that exists; it cannot
    # invent one where the owning glob emitted none).
    if isinstance(context, dict):
        run_set = context.get("python_project_modules")
        if not isinstance(run_set, set):
            run_set = set()
            context["python_project_modules"] = run_set
        run_set.update(project_modules)

    for py in parse_files:
        try:
            source_text = py.read_text(encoding="utf-8")
            tree = ast.parse(source_text, filename=str(py))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue

        rel_path = rel_to_root(py, root)
        module_path = _module_dotted_path(rel_path)
        if not module_path:
            continue

        # The table's module slot is read the way the interpreter reads it,
        # before any of the three branches that consume it: an explicit
        # relative import is arithmetic on this file's own package, done while
        # node.level is still in hand (bd zr486), and an absolute name binds
        # against the source root -- the first ancestor directory that is not
        # a package -- rather than sys.path proper (bd sigz2, z98p7).
        import_table = normalize_source_root_imports(
            _build_import_table(tree, package=package_of(module_path, py)),
            module_path=module_path,
            rel_path=rel_path,
            source_dir=py.parent,
            glob_modules=glob_modules,
        )
        visitor = _CallGraphVisitor(module_path, import_table, glob_modules)
        visitor.visit(tree)

        # Emit one symbol node per defined qualname.
        for qual, meta in visitor.symbols.items():
            sid = _symbol_id(module_path, qual)
            nodes[sid] = {
                "type": "symbol",
                "label": qual,
                "props": {
                    "file": rel_path,
                    "module": module_path,
                    "qualname": qual,
                    "line": meta["line"],
                    # ADR 0064 criterion 1: ``kind`` drawn from the
                    # python vocabulary (``class``/``function``/
                    # ``method``) declared in
                    # ``tools.tier_check_kinds._PYTHON_CANONICAL_KIND``.
                    # Without this the bundled fixture's symbols all
                    # report ``kind=None`` and criterion 6
                    # (description_coverage) cannot find any meaningful
                    # symbols to score.
                    "kind": meta["kind"],
                    # bd p6ke: the symbol's own opening docstring
                    # paragraph, always present (empty when there is no
                    # docstring) so the node shape does not vary with the
                    # source -- the same contract ``python_module`` makes
                    # for ``file:`` nodes' ``props.summary`` (bd ph1g).
                    # The read path (query_index.node_tokens,
                    # weld._match_surface) already keys on this prop
                    # generically; only the write side was missing.
                    "summary": meta["summary"],
                    "language": "python",
                    "source_strategy": "python_callgraph",
                    "authority": "derived",
                    "confidence": "definite",
                    "roles": ["implementation"],
                    "origin": "project",
                },
            }

        # Emit one inherits edge per declared base for every class.
        # ADR 0064 criterion 2 requires the edge to originate at the
        # *class symbol* (not the file node); resolution + emission is
        # delegated to ``_python_inherits.emit_inherits_edges`` to keep
        # this module under the line-count cap.
        emit_inherits_edges(
            visitor=visitor,
            module_path=module_path,
            rel_path=rel_path,
            project_modules=project_modules,
            nodes=nodes,
            edges=edges,
        )

        # Emit one calls edge per call site (deduplicated within a caller).
        # Delegated for the same line-count reason its module-scope sibling
        # is; see ``_python_calls`` for the minting and props rules.
        emit_symbol_call_edges(
            calls=visitor.calls,
            module_path=module_path,
            rel_path=rel_path,
            project_modules=project_modules,
            nodes=nodes,
            edges=edges,
        )

        # ADR 0122: decorator_list attribution (a distinct ``decorates``
        # relationship, not ``calls`` -- see the ADR for why) and
        # module-level statement calls (sourced at the ``file:`` node).
        # Class-body calls need no separate emission call: the visitor
        # already folded them into ``visitor.calls`` above.
        emit_decorates_edges(
            visitor=visitor,
            module_path=module_path,
            rel_path=rel_path,
            project_modules=project_modules,
            nodes=nodes,
            edges=edges,
        )
        emit_module_scope_call_edges(
            visitor=visitor,
            rel_path=rel_path,
            project_modules=project_modules,
            nodes=nodes,
            edges=edges,
        )
        # ADR 0127 (bd lid2): same-module bare-name VALUE references (not
        # calls) -- e.g. a class passed by name as a keyword-argument
        # value. Sourced at the referencing symbol, or the module's
        # ``file:`` anchor for a module-level statement.
        emit_reference_edges(
            visitor=visitor,
            module_path=module_path,
            rel_path=rel_path,
            project_modules=project_modules,
            nodes=nodes,
            edges=edges,
        )

    # ADR 0129 (bd mnhl): mark every caller of the terminal-sanitizer
    # chokepoint. A pure derivation over the calls edges just assembled
    # above -- no new AST walk, and correct for both a full-glob run and an
    # incremental (dirty-file) one, since each file's own resolved calls
    # already carry the whole answer for that file.
    mark_output_sink_callers(nodes, edges)

    return StrategyResult(nodes, edges, discovered_from)


def _unresolved_resolution(name: str) -> str:
    """Edge-side resolution tag for an unresolved sentinel call.

    Used by ``_resolve_call`` to populate the edge's ``props.resolution``
    string; node-side origin tagging goes through ``origin_for_sentinel``
    in ``_python_origin``.
    """
    return "builtin" if is_builtin_name(name) else "unresolved"
