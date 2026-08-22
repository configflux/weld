"""AST visitor for the python call-graph strategy.

Extracted from :mod:`weld.strategies.python_callgraph` so the parent
strategy module stays under the repo line-count cap. The visitor is
deliberately self-contained: it knows nothing about node-id minting or
graph emission -- it just records what it saw (symbols + their kinds and
own docstring summaries, class bases, per-caller call targets, decorator
targets, and module-scope call targets). The strategy's ``extract()``
function consumes those records to mint nodes and edges.

See :mod:`weld.strategies.python_callgraph` and the ADR
``weld/docs/adr/0004-call-graph-schema-extension.md`` for the
contract; ADR 0064 criterion 1 (kind vocabulary) and criterion 2
(class-level inherits edges) drive the ``kind`` + ``class_bases``
additions over the historical symbols + calls fields. ADR 0122 adds
``decorates`` (decorator_list attribution), module-scope ``calls``
(module-level statements, sourced at the ``file:`` node by the strategy
layer), and class-body ``calls`` (class-body statements, sourced at the
class's own symbol via the existing ``calls`` dict). ADR 0122's
2026-08-21 amendment (bd z0fh) extends the same bounded, scope-respecting
walk to :meth:`_CallGraphVisitor._visit_function`'s own body-call
collection, fixing a pre-existing nested-def double-count and resolving
function-nested parameter-default attribution -- see
:func:`weld.strategies._python_scope_walk._calls_in_own_scope`. ADR 0127
(bd lid2) adds ``references``: a bare-name VALUE reference (not a call,
e.g. a class named as a keyword-argument value) that resolves to a
same-module top-level symbol, via the same shared walk.
"""

from __future__ import annotations

import ast

from weld.strategies._python_anchor import symbol_summary
from weld.strategies._python_expr_resolve import _ExprResolutionMixin
from weld.strategies._python_scope_walk import _calls_in_own_scope

#: Statement types that open a new naming scope. A ``def``/``class`` found
#: inside one of these binds a name in *that* scope, not at module level.
_SCOPE_STATEMENTS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _module_level_names(module: ast.Module) -> set[str]:
    """Return every name bound by a ``def`` / ``class`` at module scope.

    Python binds all module-level names when the module executes, before
    any function body runs, so a call inside a function reaches a sibling
    ``def`` regardless of which one appears first in the file. Resolving
    against the incrementally-filled ``symbols`` dict instead made
    resolution source-order dependent and lost every backwards edge
    (bd q6yd): the callee's real symbol showed zero callers, which is the
    answer that decides whether it is safe to delete.

    Descends through module-level compound statements (``if TYPE_CHECKING``,
    ``try``, ``with``, loops) because a ``def`` inside one still binds at
    module scope -- matching what the walk itself records as a bare
    qualname -- but stops at every ``def``/``class`` boundary, whose body
    binds names in a nested scope that a bare call cannot reach. Returns a
    set, so the result cannot depend on traversal order.
    """
    names: set[str] = set()
    stack: list[ast.AST] = list(module.body)
    while stack:
        node = stack.pop()
        if isinstance(node, _SCOPE_STATEMENTS):
            names.add(node.name)
            continue
        stack.extend(ast.iter_child_nodes(node))
    return names


class _CallGraphVisitor(ast.NodeVisitor, _ExprResolutionMixin):
    """Collect symbol definitions and call sites within a single module.

    Builds seven side-effects on the orchestrator: ``symbols`` (qualname
    -> metadata including ``kind``), ``calls`` (qualname-of-caller ->
    list of resolved target ids -- now including class-body call sites,
    keyed by the class's own qualname, ADR 0122), ``class_bases`` (class
    qualname -> list of raw base names for ADR 0064 criterion-2
    inherits-edge emission), ``decorates`` (one entry per decorator_list
    expression, ADR 0122), ``module_level_calls`` (module-level
    statement call sites, ADR 0122 -- no owning symbol qualname exists for
    these, so they are not folded into ``calls``), and ``references`` /
    ``module_level_references`` (ADR 0127: resolved same-module bare-name
    VALUE references, split the same way ``calls``/``module_level_calls``
    is). Nesting is tracked via a qualname stack so methods get
    ``ClassName.method`` and closures get ``outer.inner``.
    """

    def __init__(
        self,
        module_path: str,
        import_table: dict[str, tuple[str, str]],
        project_modules: frozenset[str] = frozenset(),
    ) -> None:
        self.module_path = module_path
        self.import_table = import_table
        # Dotted module paths this run proved to be first-party (ADR 0042
        # Python rules). Used to disambiguate ``from PARENT import CHILD``
        # + ``CHILD.attr()``: when ``PARENT.CHILD`` is a known project
        # module, CHILD is a *submodule* (a namespace-package member)
        # rather than a value, so the attribute call resolves under
        # ``PARENT.CHILD``, not the bare parent ``PARENT``. Empty by
        # default so direct constructions keep the historical bare-parent
        # behaviour.
        self.project_modules = project_modules
        # qualname -> {"line": int, "name": str, "kind": str, "summary": str}
        # ``kind`` is one of ``class``, ``function``, ``method`` per the
        # python vocabulary declared in ``tools.tier_check_kinds`` and
        # consumed by ADR 0064 criterion 1. ``summary`` (bd p6ke) is the
        # symbol's own opening docstring paragraph, always present (``""``
        # when there is no docstring).
        self.symbols: dict[str, dict] = {}
        # caller-qualname -> list of (target_id, resolved, raw, line, resolution)
        self.calls: dict[str, list[tuple[str, bool, str, int, str]]] = {}
        # class qualname -> list of raw base names (simple ``ast.Name``
        # or the final segment of an ``ast.Attribute``). Empty list when
        # the ClassDef has no explicit bases (``class A:`` => no
        # implicit ``inherits -> object`` edge is emitted; the AST gives
        # us no extraction signal for that case).
        self.class_bases: dict[str, list[str]] = {}
        # One entry per ``decorator_list`` expression this run saw, at any
        # nesting depth (ADR 0122): (target_id, resolved, raw, line,
        # resolution, decorated_qual). No scope/caller attribution is
        # needed -- unlike a call, "X decorates Y" names only the
        # decorator and the symbol it decorates, both already in hand at
        # the point of definition.
        self.decorates: list[tuple[str, bool, str, int, str, str]] = []
        # Module-level statement call sites (ADR 0122): same 5-tuple shape
        # as one ``calls`` value entry, but with no owning symbol qualname
        # to key on -- the strategy layer sources these at the module's
        # ``file:`` node instead.
        self.module_level_calls: list[tuple[str, bool, str, int, str]] = []
        # ADR 0127 (bd lid2): resolved same-module value references, same
        # 5-tuple shape and same caller-qualname keying as ``calls`` --
        # see ``_collect_references``.
        self.references: dict[str, list[tuple[str, bool, str, int, str]]] = {}
        # Module-level value references (ADR 0127): sourced at ``file:``,
        # mirroring ``module_level_calls``.
        self.module_level_references: list[tuple[str, bool, str, int, str]] = []
        self._qual_stack: list[str] = []
        # Every name bound by a module-level ``def``/``class``, collected up
        # front by :meth:`visit_Module` so same-module resolution does not
        # depend on whether the callee is declared above or below the call
        # site (bd q6yd). Stays empty when the visitor is pointed at a
        # non-Module node, which leaves the historical walk-order behaviour.
        self._module_level: set[str] = set()
        # Tracks whether the *immediate* enclosing scope is a class so
        # a ``def`` directly inside a ``ClassDef`` registers as
        # ``kind=method`` rather than ``kind=function``. Deeper closures
        # (a ``def`` inside a method body) still register as ``function``.
        self._class_depth_stack: list[bool] = []

    # -- helpers ---------------------------------------------------------

    def _current_qual(self) -> str:
        return ".".join(self._qual_stack)

    def _record_symbol(
        self, name: str, lineno: int, kind: str, summary: str
    ) -> str:
        """Push *name* onto the qualname stack and record the symbol.

        ``kind`` is the canonical singular value drawn from the python
        vocabulary (``class`` / ``function`` / ``method``). ``summary``
        (bd p6ke) is the symbol's own opening docstring paragraph, from
        :func:`weld.strategies._python_anchor.symbol_summary` -- empty when
        the ``def``/``class`` has no docstring, always present as a key so a
        symbol node's shape does not vary with the source (the same
        contract :func:`weld.strategies._python_anchor.module_summary` makes
        for ``file:`` nodes). The first declaration of a qualname wins --
        later collisions keep the earlier ``kind`` and ``summary`` so a
        redefinition cannot silently downgrade ``method`` to ``function`` or
        overwrite an intentional summary with an incidental one.
        """
        self._qual_stack.append(name)
        qual = self._current_qual()
        if qual not in self.symbols:
            self.symbols[qual] = {
                "name": name, "line": lineno, "kind": kind, "summary": summary,
            }
        return qual

    # Call/decorator/reference resolution (``_resolve_call``,
    # ``_resolve_expr_target``, ``_record_decorators``,
    # ``_collect_references``) lives on ``_ExprResolutionMixin`` --
    # see :mod:`weld.strategies._python_expr_resolve`.

    # -- visit hooks -----------------------------------------------------

    def visit_Module(self, node: ast.Module) -> None:  # noqa: N802
        """Collect module-scope names and module-level call sites."""
        self._module_level = _module_level_names(node)
        # ADR 0122: a module-level statement executes at import time, in
        # no symbol's body -- sourced at the ``file:`` node by the
        # strategy layer, so recorded separately from ``calls``.
        for call in _calls_in_own_scope(node.body):
            target_id, resolved, raw, resolution = self._resolve_call(call)
            self.module_level_calls.append(
                (target_id, resolved, raw, call.lineno, resolution)
            )
        # ADR 0127: a module-level bare-name value reference has the same
        # "no owning symbol" shape a module-level call does.
        self.module_level_references.extend(self._collect_references(node.body))
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        qual = self._record_symbol(
            node.name, node.lineno, "class", symbol_summary(node)
        )
        # Capture raw base names so the orchestrator can resolve them
        # against the import table and emit ``inherits`` edges. Mirrors
        # ``weld.strategies._helpers.base_names`` -- only ``ast.Name``
        # and ``ast.Attribute`` bases produce a name; more exotic shapes
        # (subscript, call) are skipped because they are not statically
        # resolvable. ``setdefault`` keeps the first declaration's bases
        # when a class qualname is redefined (mirrors the ``symbols``
        # first-write-wins policy in ``_record_symbol``).
        if qual not in self.class_bases:
            bases: list[str] = []
            for b in node.bases:
                if isinstance(b, ast.Name):
                    bases.append(b.id)
                elif isinstance(b, ast.Attribute):
                    bases.append(b.attr)
            self.class_bases[qual] = bases
        self._record_decorators(node.decorator_list, qual)
        # ADR 0122: a class body executes once, at class-definition time --
        # the same "this executes, so a call inside it is real" reasoning
        # already applied to a function's own body -- so its own direct
        # call sites are recorded into the *existing* ``calls`` dict,
        # keyed by the class's own qualname (already a valid key whenever
        # the class has any method). Bounded to this class's own body: a
        # nested method's calls are not swept in here, they get their own
        # correct attribution below via the recursive ``visit()``.
        for call in _calls_in_own_scope(node.body):
            target_id, resolved, raw, resolution = self._resolve_call(call)
            self.calls.setdefault(qual, []).append(
                (target_id, resolved, raw, call.lineno, resolution)
            )
        # ADR 0127: a class-body bare-name value reference (e.g. a sibling
        # class named in a class-attribute tuple) is sourced at this
        # class's own symbol, same as a class-body call.
        self.references.setdefault(qual, []).extend(
            self._collect_references(node.body)
        )
        self._class_depth_stack.append(True)
        for child in node.body:
            self.visit(child)
        self._class_depth_stack.pop()
        self._qual_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._visit_function(node)

    def _visit_function(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> None:
        # ``method`` only when the *immediate* enclosing scope is a class.
        # A ``def`` nested inside another ``def`` (closure) registers as
        # ``function`` even when the outer function lives inside a class.
        in_class = bool(self._class_depth_stack and self._class_depth_stack[-1])
        kind = "method" if in_class else "function"
        qual = self._record_symbol(
            node.name, node.lineno, kind, symbol_summary(node)
        )
        self._record_decorators(node.decorator_list, qual)
        self._class_depth_stack.append(False)
        # ADR 0122 amendment (bd z0fh): a function's own body executes only
        # when the function is later CALLED -- the same "this executes, so
        # a call inside it is real" reasoning already applied to module and
        # class bodies, just gated on invocation instead of
        # import/definition time. Collection is bounded by the SAME shared
        # walker those scopes use (``_calls_in_own_scope``), not the
        # unbounded ``ast.walk(child)`` this loop used before: an unbounded
        # walk does not stop at a directly-nested def/class's own boundary,
        # so it swept that nested def's entire subtree -- body, decorators,
        # AND defaults -- into THIS function's calls too, in addition to
        # (not instead of) the nested def's own correct attribution via the
        # recursive ``visit()`` call below. The bounded walk fixes that
        # double-count and, as a side effect of the exact same boundary
        # rule module/class attribution already relies on, also attributes
        # a directly-nested def's own parameter defaults to THIS function
        # (they evaluate here, when the nested ``def`` statement runs) --
        # the function-nested case ADR 0122 Decision item 4 deferred.
        for call in _calls_in_own_scope(node.body):
            target_id, resolved, raw, resolution = self._resolve_call(call)
            self.calls.setdefault(qual, []).append(
                (target_id, resolved, raw, call.lineno, resolution)
            )
        # ADR 0127: a function-body bare-name value reference (e.g. a
        # class passed by name as a keyword-argument value, never called)
        # is sourced at this function's own symbol, same as a call.
        self.references.setdefault(qual, []).extend(
            self._collect_references(node.body)
        )
        # Descend into directly-nested defs/classes only; deeper closures
        # inside compound statements are out of scope per ADR 0004.
        for child in node.body:
            if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                self.visit(child)
        self._class_depth_stack.pop()
        self._qual_stack.pop()


__all__ = ["_CallGraphVisitor"]
