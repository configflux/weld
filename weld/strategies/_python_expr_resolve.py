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
``self.import_table``, and ``self.glob_modules`` -- state that belongs
to one visitor instance per module parsed. :class:`_CallGraphVisitor`
inherits from this alongside ``ast.NodeVisitor``.

Every resolution answers a 5-tuple whose last slot is a *hint*: the facts a
rule with the whole merged graph in front of it would need to do better than
this glob can (see :mod:`weld.strategies._python_import_attr`). It is ``None``
for every shape that resolves here, so an edge carries one only when a closure
rule still has something to decide.
"""

from __future__ import annotations

import ast

from weld.strategies._python_import_attr import make_import_attr_hint
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
    ``self.symbols``, ``self._module_level``, ``self.import_table``,
    ``self.glob_modules``, and ``self._local_aliases`` -- all set by
    ``_CallGraphVisitor.__init__``. The last is the only one that changes
    while the walk runs: it is the scope currently being swept, swapped in and
    out by the visitor's three scope hooks (bd ``80zz3``).
    """

    def _resolve_call(
        self, node: ast.Call
    ) -> tuple[str, bool, str, str, dict[str, str] | None]:
        """Best-effort resolution of a call target to a symbol id.

        Returns ``(target_id, resolved, raw, resolution, hint)``. ``resolved``
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

    def _resolve_expr_target(
        self, func: ast.expr
    ) -> tuple[str, bool, str, str, dict[str, str] | None]:
        """Best-effort resolution of *func* to a symbol id.

        Returns ``(target_id, resolved, raw, resolution, hint)``. Holds the
        resolution logic :meth:`_resolve_call` used to hold inline against
        ``node.func``; unchanged here, just parameterized on the
        expression directly so :meth:`_record_decorators` (and, per ADR
        0127, :meth:`_collect_references`) can resolve a bare (non-``Call``)
        expression the same way.

        ``hint`` is non-``None`` only for the one shape this resolver
        deliberately defers to :mod:`weld._graph_closure_import_attr`.
        """
        symbol_id, unresolved_id, unresolved_resolution = _ids()
        # Bare name: foo()
        if isinstance(func, ast.Name):
            name = func.id
            # 0. a local name this scope bound by unpacking a lazy-import
            #    accessor's return (bd 80zz3). Checked before both branches
            #    below because that is what the interpreter does: a name the
            #    scope binds shadows a module-level ``def`` and a module-level
            #    import alike, so if the two disagreed the local one is what
            #    the call reaches. They disagree only when a module rebinds a
            #    name it also imports; the table this reads through is the
            #    same one branch 2 reads, so a resolved alias can never name a
            #    module that table would not.
            aliased = self._local_aliases.get(name)
            if aliased is not None:
                module, attr = self.import_table.get(aliased, ("", ""))
                # An empty ``attr`` is the module-alias slot: the accessor
                # handed back a MODULE, whose call surface is exactly as
                # unknowable as branch 2 already says it is. Fall through.
                if attr:
                    resolution = (
                        "stdlib" if is_stdlib_module(module) else "import"
                    )
                    return symbol_id(module, attr), True, name, resolution, None
            # 1. same-module top-level def. ``symbols`` is keyed by qualname
            #    and only a *top-level* symbol has a dot-free qualname, so a
            #    bare-name hit there is already module-scope; the pre-collected
            #    ``_module_level`` set adds the callees the walk has not
            #    reached yet. Checked before the import table because a
            #    module-level ``def foo`` rebinds a same-named import, so the
            #    local definition is what a call actually reaches.
            if name in self.symbols or name in self._module_level:
                return symbol_id(self.module_path, name), True, name, "local", None
            # 2. imported name (from foo.bar import name [as alias])
            if name in self.import_table:
                module, attr = self.import_table[name]
                if attr:
                    resolution = "stdlib" if is_stdlib_module(module) else "import"
                    return symbol_id(module, attr), True, name, resolution, None
                # bare module alias used as a callable -- treat as
                # unresolved (we have no idea what the module's __call__
                # surface is)
                return (
                    unresolved_id(name), False, name,
                    unresolved_resolution(name), None,
                )
            return unresolved_id(name), False, name, unresolved_resolution(name), None

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
                # value. When ``PARENT.CHILD`` is a module THIS GLOB owns,
                # the attribute call ``CHILD.attr()`` resolves under the real
                # submodule ``PARENT.CHILD`` -- not the bare parent
                # ``PARENT``, which would mint a stray
                # ``symbol:py:PARENT:attr`` duplicate that falls through to
                # ``origin=external`` (ADR 0042 Python rules).
                #
                # ``glob_modules``, not the origin-tagging ``project_modules``:
                # that set is this glob's own on a full run and the cross-glob
                # prior-node union on an incremental one (ADR 0074), so keying
                # a *resolution* on it made the same tree resolve two different
                # ways depending on which path asked. Membership of one glob is
                # the same question on both paths; everything wider is decided
                # once, on the merged graph, by
                # :mod:`weld._graph_closure_import_attr`.
                if imported:
                    if f"{module}.{imported}" not in self.glob_modules:
                        # This glob cannot see a module by that name, so
                        # ``imported`` may name a VALUE (whose ``attr`` is a
                        # method on whatever it holds, never a sibling of
                        # ``module``) or a submodule another glob owns. See
                        # :meth:`_imported_value_attr`.
                        return self._imported_value_attr(attr, module, imported)
                    module = f"{module}.{imported}"
                resolution = "stdlib" if is_stdlib_module(module) else "import"
                return symbol_id(module, attr), True, attr, resolution, None
            # self.foo() / cls.foo() / arbitrary chains: not resolved.
            return unresolved_id(attr), False, attr, unresolved_resolution(attr), None

        # Subscript / lambda / etc -- nothing useful to record.
        return unresolved_id("<dynamic>"), False, "<dynamic>", "dynamic", None

    def _imported_value_attr(
        self, attr: str, module: str, base: str,
    ) -> tuple[str, bool, str, str, dict[str, str] | None]:
        """Refuse to resolve ``<from-imported value>.<attr>()``, and say why.

        ``_build_import_table`` distinguishes the two non-empty-slot cases
        already: ``import foo.bar as mod`` stores ``("foo.bar", "")`` -- an
        EMPTY attr slot meaning "module alias, treat the call's attribute as
        the symbol name" -- while ``from foo.bar import baz`` stores
        ``("foo.bar", "baz")``. Reading the slot is the whole fix; the branch
        above used to take the module-alias path for both and mint
        ``symbol:py:foo.bar:<attr>`` for a method on an ordinary imported
        object. On this repo that fabricated a first-party ``get`` under a
        module of protocol tables, an ``empty`` under a federation index, a
        ``finditer`` under a module of compiled regexes -- ids naming a
        function that exists under no spelling, which ``callers`` then
        answered with a confident edge. A miss is recoverable; a confident
        wrong answer is what a reader acts on.

        Why the sentinel rather than an edge to the imported name itself.
        ``TABLE.get()`` does evidence use of ``TABLE``, so retargeting to
        ``symbol:py:foo.bar:TABLE`` is tempting and reads as strictly more
        information. Measured over this repo it is not: of the ids that
        retarget would produce, the large majority name module-level
        CONSTANTS -- dicts, compiled patterns, message strings -- which are
        not symbols this strategy emits, so each becomes a fresh speculative
        stub, and the fix trades one fabricated population for a bigger one.
        Only a class base (``BM25Corpus.from_nodes()``) has a real symbol to
        land on, and telling the two apart needs to know which names the
        target module defines -- a global view a per-glob strategy does not
        have and cannot fake.

        Stdlib bases take this path too when they are spelled as a value
        import: ``pathlib`` has no top-level ``cwd``, so ``from pathlib
        import Path`` + ``Path.cwd()`` was fabricating exactly as first-party
        code was. The module-alias spelling (``import re`` + ``re.finditer()``)
        is the empty-slot case and keeps its stdlib resolution untouched.

        The same view is what the submodule reading needs when the module
        belongs to another glob. ``from weld import discover``, read from a
        file in the ``tools`` glob, cannot be proved a submodule here -- so it
        lands in this method, where it once landed on the bare parent
        ``symbol:py:weld:discover`` (fabricated: ``weld/__init__.py`` defines
        no ``discover``). Rather than answer either reading blind, the two
        names the reading turns on go onto the edge as
        ``props.import_attr`` and the sentinel stands until
        :mod:`weld._graph_closure_import_attr` -- which holds the merged
        graph, on both discover paths -- says otherwise. That is what stopped
        this shape resolving one way on a full discover and another on an
        incremental one; the class-base reading plugs into the same rule
        table.
        """
        _, unresolved_id, unresolved_resolution = _ids()
        return (
            unresolved_id(attr),
            False,
            attr,
            unresolved_resolution(attr),
            make_import_attr_hint(module, base, attr),
        )

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
            target_id, resolved, raw, resolution, hint = self._resolve_expr_target(
                target_expr
            )
            self.decorates.append(
                (
                    target_id, resolved, raw, dec.lineno, resolution,
                    decorated_qual, hint,
                )
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

        Keeping only ``"local"`` is also why these tuples carry no hint slot:
        a local hit is resolved here, in full, and there is nothing left for a
        closure rule to decide.
        """
        out: list[tuple[str, bool, str, int, str]] = []
        for name_node in _references_in_own_scope(statements):
            target_id, resolved, raw, resolution, _hint = self._resolve_expr_target(
                name_node
            )
            if resolution == "local":
                out.append((target_id, resolved, raw, name_node.lineno, resolution))
        return out


__all__ = ["_ExprResolutionMixin"]
