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
   (or ``import foo.bar``) is in scope.
3. **Unresolved fallback**: anything else becomes
   ``symbol:unresolved:<name>``. Strategies never silently drop a call.

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
from weld.strategies._python_decorates import emit_decorates_edges
from weld.strategies._python_inherits import emit_inherits_edges
from weld.strategies._python_output_sink import mark_output_sink_callers
from weld.strategies._python_references import emit_reference_edges
from weld.strategies._python_scope_calls import emit_module_scope_call_edges
from weld.strategies._python_origin import (
    is_builtin_name,
    make_resolved_target_node,
    make_sentinel_node,
    module_from_symbol_id,
    origin_for_resolved,
    origin_for_sentinel,
    project_module_set,
)

# ---------------------------------------------------------------------------
# ID helpers
# ---------------------------------------------------------------------------

#: Sentinel ID prefix for call sites whose target name could not be
#: resolved against the module's imports or local definitions. Kept stable
#: so consumers can filter / rank these distinctly from resolved targets.
UNRESOLVED_PREFIX = "symbol:unresolved:"

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

def _symbol_id(module_path: str, qualname: str) -> str:
    """Return a stable id for a Python symbol.

    Symbol IDs preserve their original module-path and qualname casing
    (ADR 0041 § Migration plan does not list ``symbol:py:*`` in the
    rename table, and lowercasing class qualnames would silently
    rewrite every symbol edge in the existing graph). The canonical
    slug rule applies to ID *prefixes* and human-name segments;
    qualnames are program identifiers and stay verbatim.
    """
    return f"symbol:py:{module_path}:{qualname}"

def _unresolved_id(name: str) -> str:
    return f"{UNRESOLVED_PREFIX}{name}"

# ---------------------------------------------------------------------------
# Import-table extraction
# ---------------------------------------------------------------------------

def _build_import_table(tree: ast.Module) -> dict[str, tuple[str, str]]:
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
    """
    table: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if not node.module:
                continue
            module = node.module
            for alias in node.names:
                local = alias.asname or alias.name
                table[local] = (module, alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
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
    project_modules: frozenset[str]
    if hint is not None:
        parse_files = dirty_matched(matched, root, hint.dirty_files)
        project_modules = reconstruct_project_modules(
            hint.prior_nodes, parse_files, root,
            module_dotted_path=_module_dotted_path,
        )
        # Decision item 4: reconstruction is an optimization; if the prior
        # graph yields no project module while there are dirty files to
        # parse (absent/empty/incompatible prior state), fall back to the
        # full-glob derivation -- correct, slightly slower -- rather than
        # mis-tag origins from an empty set.
        if not project_modules and parse_files:
            project_modules = project_module_set(
                root, matched,
                module_dotted_path=_module_dotted_path,
            )
    else:
        # Project-membership set per ADR 0042 §"Per-language detection rules"
        # (Python). The "project file set" for an extract() call is the set
        # of dotted module paths derived from the matched source files.
        # Imports whose target module matches any of these paths classify
        # as ``project``; imports outside both this set and
        # ``sys.stdlib_module_names`` classify as ``external``.
        project_modules = project_module_set(
            root,
            matched,
            module_dotted_path=_module_dotted_path,
        )

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

        import_table = _build_import_table(tree)
        visitor = _CallGraphVisitor(module_path, import_table, project_modules)
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
        for caller_qual, targets in visitor.calls.items():
            from_id = _symbol_id(module_path, caller_qual)
            seen: set[tuple[str, bool]] = set()
            for target_id, resolved, raw, line, resolution in targets:
                if (target_id, resolved) in seen:
                    continue
                seen.add((target_id, resolved))
                # Materialize unresolved sentinel nodes lazily so the
                # graph stays referentially closed for the orchestrator's
                # final cleanup pass. Resolved cross-module targets are
                # likewise speculatively minted so consumers always get
                # an ``origin``-tagged node for every edge endpoint
                # (ADR 0042 Python rules).
                if target_id.startswith(UNRESOLVED_PREFIX):
                    nodes.setdefault(
                        target_id,
                        make_sentinel_node(
                            target_id, resolution, origin_for_sentinel(resolution)
                        ),
                    )
                else:
                    target_module = module_from_symbol_id(target_id)
                    nodes.setdefault(
                        target_id,
                        make_resolved_target_node(
                            target_id,
                            origin_for_resolved(target_module, project_modules),
                        ),
                    )
                edges.append(
                    {
                        "from": from_id,
                        "to": target_id,
                        "type": "calls",
                        "props": {
                            "source_strategy": "python_callgraph",
                            "confidence": "definite" if resolved else "speculative",
                            "resolved": resolved,
                            "raw": raw,
                            "resolution": resolution,
                            "provenance": {
                                "file": rel_path,
                                "line": line,
                            },
                        },
                    }
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
