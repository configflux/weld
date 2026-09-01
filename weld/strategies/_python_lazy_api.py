"""Read a local name bound by unpacking a lazy-import accessor's return.

bd ``80zz3``. This repo defers an import into a function on purpose -- to keep a
shape (a symbol-id spelling, an import-table spelling) defined in exactly one
place while avoiding a circular import at module load -- and then unpacks what
the accessor returns::

    def _callgraph_api():
        from weld.strategies.python_callgraph import _build_import_table, _symbol_id
        return _symbol_id, _build_import_table

    _symbol_id, build_import_table = _callgraph_api()
    build_import_table(tree, package=...)

The ``from ... import`` half was never the problem: ``_build_import_table``
walks the whole module, so a function-scoped import is already a table entry and
a call by the *imported* name already resolves. The local name is the miss. It
appears in no import table, so the call fell to a ``symbol:unresolved:``
sentinel and ``wd callers`` on the real definition reported one caller where two
exist -- on ``python_callgraph``'s own contract surface, which is exactly where
"who calls this" gets asked before a signature changes.

**What licenses reading the accessor, and what stops this becoming a dataflow
analyzer.** A value returned from a function is not knowable in general, and
guessing one is the fabrication class bd ``sigz2``/``zr486`` removed. Nothing is
guessed here: the accessor's whole body is imports and one ``return`` of names
those imports bound, so its value is *written in the source* and is read, not
inferred. Every shape outside that is refused, and the refusal is silence -- the
name keeps resolving exactly as it did before, so this can only ever add an edge
that was already spelled out, never redirect one.

The rule is bounded by the same three things at once, and each one alone would
sink a wrong answer:

* **The accessor is a module-level ``def`` in the file being read.** Not
  imported, not nested, not decorated, not redefined -- the returned names must
  be readable from this one AST.
* **Each returned name is bound by exactly one import in the whole module, and
  that import is the accessor's own.** The alias then resolves through the
  module import table -- which already carries the ``sigz2`` and ``zr486``
  corrections to the module slot -- so this cannot mint an id for a module that
  exists under no spelling. A second import binding the same name makes the
  table entry ambiguous, and ambiguity is refused rather than picked.
  Requiring the accessor's *own* import is a deliberate under-report: a name
  the accessor closes over from module scope would often be the same symbol,
  but proving it means proving nothing at module scope rebinds the name, and a
  module-level import needs no accessor to be followed in the first place --
  the direct call already resolves.
* **The local name is bound once in its own scope, by the unpack.** A rebound
  name, a same-named parameter, a second unpack from a different accessor: all
  refused. The count is taken with an unbounded walk on purpose -- over-counting
  a nested scope's binding only ever produces a refusal, which is the direction
  a wrong count is allowed to be wrong in.

:func:`import_alias_names` lives here rather than being spelled twice: it is the
alias-naming half of ``python_callgraph._build_import_table``, which imports it
back from this module. The dependency runs one way only (this module knows
nothing of the strategy), so there is no cycle to defer.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator

from weld.strategies._python_scope_walk import _bounded_scope_nodes

#: Statement types that open a new naming scope -- the boundary
#: :func:`_module_level_defs` stops at, mirroring
#: ``_python_callgraph_visitor._module_level_names``.
_SCOPE_STATEMENTS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def import_alias_names(
    node: ast.Import | ast.ImportFrom,
) -> Iterator[tuple[str, ast.alias]]:
    """Yield ``(local_name, alias)`` for every name *node* binds.

    The two spellings differ and the difference matters: ``from foo.bar import
    baz`` binds ``baz``, while ``import foo.bar`` binds only the *first*
    segment, ``foo``. ``python_callgraph._build_import_table`` and this module's
    accessor check both need that answer, and a second copy of it could drift
    into a lookup that silently misses -- so it is written once, here, and the
    table builder imports it.
    """
    for alias in node.names:
        if isinstance(node, ast.ImportFrom):
            yield alias.asname or alias.name, alias
        else:
            yield alias.asname or alias.name.split(".")[0], alias


def _bare_names(expr: ast.expr | None) -> tuple[str, ...] | None:
    """Return the bare names *expr* is made of, or None if it holds anything else.

    Read from both ends of the idiom, because both ends have to be the same
    shape for the positions to line up: the accessor's returned expression, and
    the assignment target the caller unpacks it into. An attribute or subscript
    binds (or names) no local; a starred element stands for a *list* of whatever
    is left over, which is neither one of the returned callables nor a fixed
    position, so it would shift every name after it.
    """
    if isinstance(expr, ast.Name):
        return (expr.id,)
    if isinstance(expr, (ast.Tuple, ast.List)) and expr.elts:
        names = tuple(e.id for e in expr.elts if isinstance(e, ast.Name))
        return names if len(names) == len(expr.elts) else None
    return None


def _module_level_defs(module: ast.Module) -> dict[str, list[ast.AST]]:
    """Map every module-scope ``def``/``class`` name to the nodes declaring it.

    Descends module-level compound statements (``if TYPE_CHECKING``, ``try``,
    ``with``, loops) because a ``def`` inside one still binds at module scope,
    and stops at every ``def``/``class`` boundary, whose body binds elsewhere --
    the same boundary ``_module_level_names`` walks for call resolution. The
    value is a *list* so a redefined name is visible as such: two declarations
    mean the name's meaning depends on execution order, and an accessor whose
    body cannot be read off one node is not read at all.
    """
    found: dict[str, list[ast.AST]] = {}
    stack: list[ast.AST] = list(module.body)
    while stack:
        node = stack.pop()
        if isinstance(node, _SCOPE_STATEMENTS):
            found.setdefault(node.name, []).append(node)
            continue
        stack.extend(ast.iter_child_nodes(node))
    return found


def _import_binding_counts(module: ast.Module) -> dict[str, int]:
    """Count how many import statements in *module* bind each local name.

    Module-wide and unbounded, matching exactly what ``_build_import_table``
    reads: the table has one slot per local name, so a name two imports bind
    resolves to whichever the walk wrote last. That is fine for the table's own
    purposes and fatal for this one -- the accessor's guarantee is that *its*
    import is the entry -- so any name with a second binding site is refused.
    """
    counts: dict[str, int] = {}
    for node in ast.walk(module):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for local, _alias in import_alias_names(node):
                counts[local] = counts.get(local, 0) + 1
    return counts


def _accessor_shape(fn: ast.AST) -> tuple[tuple[str, ...], set[str]] | None:
    """Return *fn*'s ``(returned names, names its own imports bind)``, or None.

    The shape, and nothing else: a plain (non-async, undecorated) ``def`` whose
    body is an optional docstring, one or more import statements, and a single
    ``return`` of a bare name or a non-empty tuple/list of bare names. A
    decorator is refused because it can replace the function outright; an
    ``async def`` because its value is reached through ``await``, which is not
    the assignment shape :func:`local_alias_bindings` matches.

    *fn*'s arguments are deliberately not examined. The body computes nothing,
    so whatever is passed cannot change what comes back -- the return is the
    same tuple for every call, which is the property being relied on.
    """
    if not isinstance(fn, ast.FunctionDef) or fn.decorator_list:
        return None
    body = list(fn.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    if len(body) < 2 or not isinstance(body[-1], ast.Return):
        return None
    own: set[str] = set()
    for st in body[:-1]:
        if not isinstance(st, (ast.Import, ast.ImportFrom)):
            return None
        own.update(local for local, _alias in import_alias_names(st))
    returns = _bare_names(body[-1].value)
    if returns is None:
        return None
    return returns, own


def _binding_counts(statements: list[ast.stmt]) -> dict[str, int]:
    """Count every binding of every name reachable from *statements*.

    Unbounded on purpose: a nested scope's bindings are counted here even though
    they are not this scope's, because the only use of this count is to *refuse*
    a name bound more than once, and an over-count can only refuse more. An
    under-count is what would let a rebound name keep pointing at the accessor's
    import.
    """
    counts: dict[str, int] = {}

    def bump(name: str) -> None:
        counts[name] = counts.get(name, 0) + 1

    for stmt in statements:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Name) and isinstance(
                node.ctx, (ast.Store, ast.Del)
            ):
                bump(node.id)
            elif isinstance(node, _SCOPE_STATEMENTS):
                bump(node.name)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for local, _alias in import_alias_names(node):
                    bump(local)
            elif isinstance(node, ast.arg):
                bump(node.arg)
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                for declared in node.names:
                    bump(declared)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                bump(node.name)
            elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
                bump(node.name)
            elif isinstance(node, ast.MatchMapping) and node.rest:
                bump(node.rest)
    return counts


def lazy_api_accessors(module: ast.Module) -> dict[str, tuple[str, ...]]:
    """Map each lazy-import accessor in *module* to the names it returns.

    See the module docstring for the three bounds every entry clears. The shape
    test comes first and the two module-wide walks after it, so a module that
    defines no such function -- the overwhelming majority -- pays for neither,
    and :func:`local_alias_bindings` then does no work at all.
    """
    candidates: dict[str, tuple[tuple[str, ...], set[str]]] = {}
    for name, nodes in _module_level_defs(module).items():
        shape = _accessor_shape(nodes[0]) if len(nodes) == 1 else None
        if shape is not None:
            candidates[name] = shape
    if not candidates:
        return {}
    imports = _import_binding_counts(module)
    # The accessor's own name has to mean the ``def`` and nothing else: a
    # module that rebinds it -- ``acc = wrap(acc)``, the hand-applied form of
    # the decorator :func:`_accessor_shape` already refuses -- is returning
    # something this body does not describe.
    bindings = _binding_counts(module.body)
    return {
        name: returns
        for name, (returns, own) in candidates.items()
        if bindings.get(name, 0) == 1
        and all(r in own and imports.get(r, 0) == 1 for r in returns)
    }


def local_alias_bindings(
    statements: list[ast.stmt],
    accessors: dict[str, tuple[str, ...]],
    *,
    already_bound: frozenset[str] = frozenset(),
) -> dict[str, str]:
    """Map a name *statements* binds from an accessor to the name it holds.

    The returned value is the *imported* name, not a resolved target: the caller
    holds the module import table, which is where the ``sigz2``/``zr486``
    corrections to the module slot live, and resolving through it is what keeps
    this rule incapable of naming a module the table would not.

    Scoped to the statements given, through the same boundary walk that call and
    reference collection use, so an unpack inside a nested ``def`` belongs to
    that ``def`` and not to this scope. *already_bound* carries names the scope
    binds outside its own statements -- a function's parameters -- which
    :func:`_binding_counts` cannot see from the body alone.
    """
    if not accessors:
        return {}
    bound: dict[str, str] = {}
    for node in _bounded_scope_nodes(statements):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        call = node.value
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
            continue
        returns = accessors.get(call.func.id)
        names = _bare_names(node.targets[0])
        if returns is None or names is None or len(names) != len(returns):
            continue
        for local, imported in zip(names, returns):
            bound[local] = imported
    if not bound:
        return {}
    counts = _binding_counts(statements)
    return {
        local: imported
        for local, imported in bound.items()
        if counts.get(local, 0) == 1 and local not in already_bound
    }


__all__ = ["import_alias_names", "lazy_api_accessors", "local_alias_bindings"]
