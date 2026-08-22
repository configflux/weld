"""Expression-target resolution for the python call-graph visitor.

Carved out of :mod:`weld.strategies._python_callgraph_visitor` to keep that
module under the repo line-count cap. This mixin owns one question --
"what symbol does this expression name" -- and the collectors built
directly on top of that single answer: decorator attribution (ADR 0122)
and same-module bare-name value references (ADR 0127, bd lid2). The
visitor itself keeps the separate responsibility of AST dispatch and
symbol registration.

A mixin, not free functions, because the resolution logic reads
``self.module_path``, ``self.symbols``, ``self._module_level``,
``self.import_table``, and ``self.project_modules`` -- state that belongs
to one visitor instance per module parsed. :class:`_CallGraphVisitor`
inherits from this alongside ``ast.NodeVisitor``.
"""

from __future__ import annotations

import ast

from weld.strategies._python_origin import is_stdlib_module
from weld.strategies._python_scope_walk import _references_in_own_scope


# These helpers live on the strategy module so the symbol-id shape is
# defined in exactly one place; resolution imports them lazily inside
# methods to avoid a circular import at module load time.
def _ids():
    from weld.strategies.python_callgraph import (
        _symbol_id,
        _unresolved_id,
        _unresolved_resolution,
    )

    return _symbol_id, _unresolved_id, _unresolved_resolution


class _ExprResolutionMixin:
    """Resolution methods for ``_CallGraphVisitor`` (see module docstring).

    Assumes the final class also provides ``self.module_path``,
    ``self.symbols``, ``self._module_level``, ``self.import_table``, and
    ``self.project_modules`` -- all set by ``_CallGraphVisitor.__init__``.
    """

    def _resolve_call(self, node: ast.Call) -> tuple[str, bool, str, str]:
        """Best-effort resolution of a call target to a symbol id.

        Returns ``(target_id, resolved, raw, resolution)``. ``resolved``
        is True for same-module / import-table hits and False for the
        unresolved sentinel form. Thin wrapper over
        :meth:`_resolve_expr_target` -- kept as a distinct method so a
        call site reads as resolving a *call*, while decorator resolution
        (:meth:`_record_decorators`) reads as resolving an *expression*,
        sharing the exact same Name/Attribute logic (ADR 0122: a
        decorator expression needs the same resolution power as a call
        target, since any expression legal as a call target is also legal
        in ``decorator_list``).
        """
        return self._resolve_expr_target(node.func)

    def _resolve_expr_target(self, func: ast.expr) -> tuple[str, bool, str, str]:
        """Best-effort resolution of *func* to a symbol id.

        Returns ``(target_id, resolved, raw, resolution)``. Holds the
        resolution logic :meth:`_resolve_call` used to hold inline against
        ``node.func``; unchanged here, just parameterized on the
        expression directly so :meth:`_record_decorators` (and, per ADR
        0127, :meth:`_collect_references`) can resolve a bare (non-``Call``)
        expression the same way.
        """
        symbol_id, unresolved_id, unresolved_resolution = _ids()
        # Bare name: foo()
        if isinstance(func, ast.Name):
            name = func.id
            # 1. same-module top-level def. ``symbols`` is keyed by qualname
            #    and only a *top-level* symbol has a dot-free qualname, so a
            #    bare-name hit there is already module-scope; the pre-collected
            #    ``_module_level`` set adds the callees the walk has not
            #    reached yet. Checked before the import table because a
            #    module-level ``def foo`` rebinds a same-named import, so the
            #    local definition is what a call actually reaches.
            if name in self.symbols or name in self._module_level:
                return symbol_id(self.module_path, name), True, name, "local"
            # 2. imported name (from foo.bar import name [as alias])
            if name in self.import_table:
                module, attr = self.import_table[name]
                if attr:
                    resolution = "stdlib" if is_stdlib_module(module) else "import"
                    return symbol_id(module, attr), True, name, resolution
                # bare module alias used as a callable -- treat as
                # unresolved (we have no idea what the module's __call__
                # surface is)
                return unresolved_id(name), False, name, unresolved_resolution(name)
            return unresolved_id(name), False, name, unresolved_resolution(name)

        # Attribute call: a.b() or a.b.c()
        if isinstance(func, ast.Attribute):
            attr = func.attr
            # x.y() where x is an imported module / module alias
            value = func.value
            if isinstance(value, ast.Name) and value.id in self.import_table:
                module, imported = self.import_table[value.id]
                # ``from PARENT import CHILD`` records ``(PARENT, CHILD)``.
                # CHILD may be a *submodule* (a namespace-package member,
                # e.g. ``from tools import tier1_corpus``) rather than a
                # value. When ``PARENT.CHILD`` is a module this run proved
                # first-party, the attribute call ``CHILD.attr()`` resolves
                # under the real submodule ``PARENT.CHILD`` -- not the bare
                # parent ``PARENT``, which would mint a stray
                # ``symbol:py:PARENT:attr`` duplicate that falls through to
                # ``origin=external`` (ADR 0042 Python rules).
                if imported:
                    submodule = f"{module}.{imported}"
                    if submodule in self.project_modules:
                        module = submodule
                resolution = "stdlib" if is_stdlib_module(module) else "import"
                return symbol_id(module, attr), True, attr, resolution
            # self.foo() / cls.foo() / arbitrary chains: not resolved.
            return unresolved_id(attr), False, attr, unresolved_resolution(attr)

        # Subscript / lambda / etc -- nothing useful to record.
        return unresolved_id("<dynamic>"), False, "<dynamic>", "dynamic"

    def _record_decorators(
        self, decorator_list: list[ast.expr], decorated_qual: str
    ) -> None:
        """Record a ``decorates`` fact for every entry in *decorator_list*.

        ADR 0122. Each decorator expression executes in the ENCLOSING scope
        at definition time, applied to -- not called by -- the symbol it
        decorates (``f = deco(f)``), so this is a distinct relationship
        from ``calls``, not a caller attribution: no scope/qualname
        tracking is needed, unlike :meth:`_visit_function`'s call sweep.

        A Call-shaped decorator (``@app.route('/x')``, ``@lru_cache()``)
        resolves its ``.func`` -- the thing actually invoked to produce the
        wrapper. A bare decorator (``@staticmethod``, ``@retry``) resolves
        itself directly, since there is no ``Call`` node to unwrap; the
        same :meth:`_resolve_expr_target` handles both.
        """
        for dec in decorator_list:
            target_expr = dec.func if isinstance(dec, ast.Call) else dec
            target_id, resolved, raw, resolution = self._resolve_expr_target(
                target_expr
            )
            self.decorates.append(
                (target_id, resolved, raw, dec.lineno, resolution, decorated_qual)
            )

    def _collect_references(
        self, statements: list[ast.stmt]
    ) -> list[tuple[str, bool, str, int, str]]:
        """Resolve every same-module value reference in *statements*.

        ADR 0127 (bd lid2): shares :meth:`_resolve_expr_target` with
        ``calls``/``decorates`` (ADR 0113/0119's one-resolver precedent,
        applied again) but keeps only its ``"local"`` branch -- a
        same-module top-level ``def``/``class``. An import-table hit or an
        unresolved name is silently dropped, not recorded as a sentinel:
        unlike a call target, a value reference that does not resolve
        locally carries no signal worth a graph node, and this repo alone
        has thousands of bare-``Name`` loads (parameters, locals,
        builtins) that would otherwise flood the graph for zero benefit.
        """
        out: list[tuple[str, bool, str, int, str]] = []
        for name_node in _references_in_own_scope(statements):
            target_id, resolved, raw, resolution = self._resolve_expr_target(
                name_node
            )
            if resolution == "local":
                out.append((target_id, resolved, raw, name_node.lineno, resolution))
        return out


__all__ = ["_ExprResolutionMixin"]
