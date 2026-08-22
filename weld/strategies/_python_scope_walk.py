"""Bounded call-site and value-reference walks for a scope's own direct
statements (ADR 0122, ADR 0127).

Carved out of :mod:`weld.strategies._python_callgraph_visitor` to keep that
module under the repo line-count cap -- these are pure, ``self``-free
functions with no dependency on the visitor's instance state, so they move
cleanly.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator


def _bounded_scope_nodes(statements: list[ast.stmt]) -> Iterator[ast.AST]:
    """Yield every AST node reachable from *statements* in THIS scope.

    Shared boundary walk behind :func:`_calls_in_own_scope` (ADR 0122) and
    :func:`_references_in_own_scope` (ADR 0127, bd lid2): both need the
    identical "what belongs to this scope" answer, and bd z0fh already
    showed what happens when two call sites grow their own, slightly
    different copies of it (a directly-nested def's body got swept twice,
    by two different mechanisms, before that fix unified them). One walk,
    two thin filters on top, so the boundary rule can only drift once for
    both.

    A nested ``def``/``class``'s own *body* belongs to its own scope,
    entered only when the visitor's recursive dispatch reaches it
    separately, so this walk does not cross into it. A boundary
    ``FunctionDef``/``AsyncFunctionDef``'s parameter defaults
    (``args.defaults`` / ``args.kw_defaults``) are the one exception: they
    evaluate in THIS scope, at the moment the ``def`` statement itself
    runs, so they are still collected here. Its ``decorator_list`` also
    runs in this scope but is never yielded here -- decorator attribution
    is a distinct relationship (``decorates``) recorded separately by
    ``_CallGraphVisitor._record_decorators``. ``if``/``for``/``try``/
    ``with`` do not open a new Python scope, matching
    ``_module_level_names``.

    Two positions are structurally excluded from ever being treated as a
    bare-name VALUE reference, because both already mean something else: a
    ``Call``'s own ``.func`` (that expression is invoked, not referenced --
    ADR 0127's whole reason for existing; already a ``calls`` edge via
    this same walk's Call-collection) and an ``Attribute``'s bare-``Name``
    base (``self`` in ``self.foo``, ``mod`` in ``mod.CONST`` -- navigating
    *through* a name is not a reference to it, and attribute-shaped access
    is explicitly out of ADR 0127's scope). Both positions are still fully
    explored for anything else they might contain -- a chained call
    (``foo().bar()``), a nested attribute access, a call buried in an
    attribute's own value expression -- only the direct excluded node
    itself is skipped, so ``_calls_in_own_scope``'s existing reach
    (including through such chains) is unchanged by this exclusion.
    """
    stack: list[ast.AST] = list(statements)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for default in (*node.args.defaults, *node.args.kw_defaults):
                if default is not None:
                    stack.append(default)
            continue
        if isinstance(node, ast.ClassDef):
            continue
        yield node
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                stack.append(node.func)
            stack.extend(node.args)
            stack.extend(node.keywords)
            continue
        if isinstance(node, ast.Attribute):
            if not isinstance(node.value, ast.Name):
                stack.append(node.value)
            continue
        stack.extend(ast.iter_child_nodes(node))


def _calls_in_own_scope(statements: list[ast.stmt]) -> list[ast.Call]:
    """Return every ``Call`` reachable from *statements* in THIS scope.

    A module-level statement, a class-body statement, and a function body
    statement all execute in the scope that contains them -- at import time
    for a module, at class-definition time for a class body, at call time
    for a function body -- so a call found there is real for THAT scope,
    regardless of when the scope itself runs. This walker collects that
    population for all three callers (``_CallGraphVisitor.visit_Module``,
    ``_CallGraphVisitor.visit_ClassDef``, and
    ``_CallGraphVisitor._visit_function`` -- the last since the ADR 0122
    2026-08-21 amendment, bd z0fh; previously ``_visit_function`` used its
    own unbounded ``ast.walk(child)`` sweep, which did not stop at a
    directly-nested def/class's own boundary and so double-attributed that
    nested def's own body calls to the outer function).

    See :func:`_bounded_scope_nodes` for the shared boundary rule (nested
    def/class bodies excluded, boundary defaults included, decorator_list
    excluded, ``if``/``for``/``try``/``with`` transparent).
    """
    return [n for n in _bounded_scope_nodes(statements) if isinstance(n, ast.Call)]


def _references_in_own_scope(statements: list[ast.stmt]) -> list[ast.Name]:
    """Return every bare-name VALUE reference reachable from *statements*.

    ADR 0127 (bd lid2): a ``Name`` in ``Load`` context that is not a
    ``Call``'s own callee and not an ``Attribute``'s bare base -- see
    :func:`_bounded_scope_nodes` for exactly which two positions those
    exclusions cover and why. ``Store``/``Del`` contexts (an assignment
    target, a ``del`` statement) are never references -- only a read
    counts.

    Resolution (same-module vs. cross-module vs. unresolved) and the
    same-module-only acceptance filter are the visitor's job, via the same
    ``_CallGraphVisitor._resolve_expr_target`` a call target uses, not this
    walk's -- this function's contract is purely "what is a candidate",
    matching how :func:`_calls_in_own_scope` also returns every ``Call``
    node regardless of whether it later resolves.
    """
    return [
        n for n in _bounded_scope_nodes(statements)
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
    ]


__all__ = ["_calls_in_own_scope", "_references_in_own_scope"]
