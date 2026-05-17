"""AST visitor for the python call-graph strategy.

Extracted from :mod:`weld.strategies.python_callgraph` so the parent
strategy module stays under the repo line-count cap. The visitor is
deliberately self-contained: it knows nothing about node-id minting or
graph emission -- it just records what it saw (symbols + their kinds,
class bases, and per-caller call targets). The strategy's ``extract()``
function consumes those records to mint nodes and edges.

See :mod:`weld.strategies.python_callgraph` and the ADR
``weld/docs/adr/0004-call-graph-schema-extension.md`` for the
contract; ADR 0064 criterion 1 (kind vocabulary) and criterion 2
(class-level inherits edges) drive the ``kind`` + ``class_bases``
additions over the historical symbols + calls fields.
"""

from __future__ import annotations

import ast

from weld.strategies._python_origin import is_stdlib_module


# These helpers live on the strategy module so the symbol-id shape is
# defined in exactly one place; the visitor imports them lazily inside
# methods to avoid a circular import at module load time.
def _ids():
    from weld.strategies.python_callgraph import (
        _symbol_id,
        _unresolved_id,
        _unresolved_resolution,
    )

    return _symbol_id, _unresolved_id, _unresolved_resolution


class _CallGraphVisitor(ast.NodeVisitor):
    """Collect symbol definitions and call sites within a single module.

    Builds three side-effects on the orchestrator: ``symbols`` (qualname
    -> metadata including ``kind``), ``calls`` (qualname-of-caller ->
    list of resolved target ids), and ``class_bases`` (class qualname
    -> list of raw base names for ADR 0064 criterion-2 inherits-edge
    emission). Nesting is tracked via a qualname stack so methods get
    ``ClassName.method`` and closures get ``outer.inner``.
    """

    def __init__(
        self,
        module_path: str,
        import_table: dict[str, tuple[str, str]],
    ) -> None:
        self.module_path = module_path
        self.import_table = import_table
        # qualname -> {"line": int, "name": str, "kind": str}
        # ``kind`` is one of ``class``, ``function``, ``method`` per the
        # python vocabulary declared in ``tools.tier_check_kinds`` and
        # consumed by ADR 0064 criterion 1.
        self.symbols: dict[str, dict] = {}
        # caller-qualname -> list of (target_id, resolved, raw, line, resolution)
        self.calls: dict[str, list[tuple[str, bool, str, int, str]]] = {}
        # class qualname -> list of raw base names (simple ``ast.Name``
        # or the final segment of an ``ast.Attribute``). Empty list when
        # the ClassDef has no explicit bases (``class A:`` => no
        # implicit ``inherits -> object`` edge is emitted; the AST gives
        # us no extraction signal for that case).
        self.class_bases: dict[str, list[str]] = {}
        self._qual_stack: list[str] = []
        # Tracks whether the *immediate* enclosing scope is a class so
        # a ``def`` directly inside a ``ClassDef`` registers as
        # ``kind=method`` rather than ``kind=function``. Deeper closures
        # (a ``def`` inside a method body) still register as ``function``.
        self._class_depth_stack: list[bool] = []

    # -- helpers ---------------------------------------------------------

    def _current_qual(self) -> str:
        return ".".join(self._qual_stack)

    def _record_symbol(self, name: str, lineno: int, kind: str) -> str:
        """Push *name* onto the qualname stack and record the symbol.

        ``kind`` is the canonical singular value drawn from the python
        vocabulary (``class`` / ``function`` / ``method``). The first
        declaration of a qualname wins -- later collisions keep the
        earlier ``kind`` so a redefinition cannot silently downgrade
        ``method`` to ``function``.
        """
        self._qual_stack.append(name)
        qual = self._current_qual()
        if qual not in self.symbols:
            self.symbols[qual] = {"name": name, "line": lineno, "kind": kind}
        return qual

    def _resolve_call(self, node: ast.Call) -> tuple[str, bool, str, str]:
        """Best-effort resolution of a call target to a symbol id.

        Returns ``(target_id, resolved, raw, resolution)``. ``resolved``
        is True for same-module / import-table hits and False for the
        unresolved sentinel form.
        """
        symbol_id, unresolved_id, unresolved_resolution = _ids()
        func = node.func
        # Bare name: foo()
        if isinstance(func, ast.Name):
            name = func.id
            # 1. same-module top-level def
            if name in self.symbols:
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
                module, _ = self.import_table[value.id]
                resolution = "stdlib" if is_stdlib_module(module) else "import"
                return symbol_id(module, attr), True, attr, resolution
            # self.foo() / cls.foo() / arbitrary chains: not resolved.
            return unresolved_id(attr), False, attr, unresolved_resolution(attr)

        # Subscript / lambda / etc -- nothing useful to record.
        return unresolved_id("<dynamic>"), False, "<dynamic>", "dynamic"

    # -- visit hooks -----------------------------------------------------

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        qual = self._record_symbol(node.name, node.lineno, "class")
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
        qual = self._record_symbol(node.name, node.lineno, kind)
        self._class_depth_stack.append(False)
        # Walk the body for Call nodes; nested functions / classes are
        # handled by recursive visit_*.
        for child in node.body:
            for sub in ast.walk(child):
                if isinstance(sub, ast.Call):
                    target_id, resolved, raw, resolution = self._resolve_call(sub)
                    self.calls.setdefault(qual, []).append(
                        (target_id, resolved, raw, sub.lineno, resolution)
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
