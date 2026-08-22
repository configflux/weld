"""The literal subset of Starlark that BUILD-file parsing evaluates (ADR 0105).

Split out of :mod:`weld.strategies._bazel_starlark` when ``load()`` support
pushed that module past the 400-line cap (CLAUDE.md "Line-Count Policy"). The
seam is a real one rather than a convenience: everything here evaluates an
*expression* against a namespace and knows nothing about rules, targets, or
packages, while the module it came from walks *declarations* and knows nothing
about how a value is computed.

The evaluator is deliberately total and deliberately narrow. Every construct
outside the subset -- a call (``glob``, ``select``, a macro), an attribute, a
format string, arithmetic on non-literals -- evaluates to
:data:`UNEVALUATABLE`, and every caller treats that as "contributes nothing"
rather than as a value to guess at. That is the ``_target_ids`` lesson: a
wrong-but-real entry is worse than a missing one, because nothing downstream
can tell it from a real one.
"""

from __future__ import annotations

import ast

#: Value returned by the evaluator for an expression it cannot resolve.
#: ``None`` is unambiguous here because no supported construct evaluates to it.
UNEVALUATABLE = None

#: Reserved binding under which a caller supplies the ``glob()`` resolver
#: (bd mhn7). It lives in *bindings* rather than in a parameter threaded through
#: every recursive call because ``glob`` is resolved exactly where any other
#: name is, and the spelling cannot collide with a Starlark identifier: ``(`` is
#: not a legal character in one, so no BUILD file can bind this key.
GLOB_RESOLVER_KEY = "glob()"


def _is_glob_call(func: ast.expr) -> bool:
    """True for either legal spelling of a ``glob`` call.

    ``glob(...)`` is how a BUILD file spells it; ``native.glob(...)`` is the
    *only* spelling a ``.bzl`` macro body can legally use -- a bare ``glob``
    name is not in scope there, the same way a bare ``py_test`` is not (bd
    x9lg). The two spellings must resolve identically because this evaluator
    does not know or care which kind of file the call text came from -- only
    whether a resolver is installed in *bindings*, exactly the asymmetry
    :func:`weld.strategies._bazel_starlark._rule_name` already draws for rule
    calls.
    """
    if isinstance(func, ast.Name):
        return func.id == "glob"
    return (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "native"
        and func.attr == "glob"
    )


def eval_kwarg(call: ast.Call, key: str, bindings: dict):
    """Evaluate keyword *key* of *call*, or return :data:`UNEVALUATABLE`.

    A ``**kwargs``-splatted keyword (``keyword.arg is None``) is searched
    second, only when no explicit keyword named *key* is present. A macro
    body that passes its own ``**kwargs`` straight through
    (``py_test(name = name, **kwargs)``) puts every call-site attribute its
    signature did not name explicitly -- ``srcs``, ``deps``, ``data`` for
    ``weld/tests/bench/bench_py_test.bzl`` -- inside that dict, and this is
    the one place it is ever read back out (ADR 0109 amendment, bd iysm).
    ``bind_macro_call`` (``_bazel_macro_args.py``) is the only caller that
    ever binds a ``dict`` into *bindings*; no Starlark construct this module
    evaluates produces one, so an explicit keyword always wins when both
    exist.
    """
    for keyword in call.keywords:
        if keyword.arg == key:
            return eval_expr(keyword.value, bindings)
    for keyword in call.keywords:
        if keyword.arg is None:
            value = eval_expr(keyword.value, bindings)
            if isinstance(value, dict) and key in value:
                return value[key]
    return UNEVALUATABLE


def eval_string_list(call: ast.Call, key: str, bindings: dict) -> list[str]:
    """Evaluate keyword *key* as a list of strings, dropping what will not resolve."""
    value = eval_kwarg(call, key, bindings)
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, str)]


def _eval_glob(node: ast.Call, bindings: dict):
    """Evaluate a ``glob()`` call against the resolver in *bindings*.

    Returns :data:`UNEVALUATABLE` unless a resolver is installed **and** every
    argument is itself evaluatable -- an ``include`` list weld cannot read is a
    reason to contribute nothing, never a reason to glob a guess. ``exclude``
    is treated the same way: silently ignoring one that will not evaluate would
    hand back a *superset* of the real membership, which is the invent-a-member
    failure this whole path exists to avoid.
    """
    resolver = bindings.get(GLOB_RESOLVER_KEY)
    if resolver is None:
        return UNEVALUATABLE
    if node.args:
        include = eval_expr(node.args[0], bindings)
    else:
        include = eval_kwarg(node, "include", bindings)
    if not isinstance(include, list) or not all(
        isinstance(p, str) for p in include
    ):
        return UNEVALUATABLE

    exclude: list = []
    for keyword in node.keywords:
        if keyword.arg == "exclude":
            exclude = eval_expr(keyword.value, bindings)
            if not isinstance(exclude, list) or not all(
                isinstance(p, str) for p in exclude
            ):
                return UNEVALUATABLE
        elif keyword.arg == "exclude_directories":
            # Only the default (1, files only) is modelled. ``0`` would add
            # directory members this walk does not collect, so rather than
            # under-report a set that looks complete, decline the call.
            if not isinstance(keyword.value, ast.Constant):
                return UNEVALUATABLE
            if keyword.value.value != 1:
                return UNEVALUATABLE
    return resolver(include, exclude)


def eval_expr(node: ast.AST, bindings: dict):
    """Evaluate *node* against *bindings*.

    Returns a ``str``, a ``list``, or :data:`UNEVALUATABLE`. *bindings* carries
    whatever names are in scope: a comprehension's loop variable, a module's
    own assignments, and the symbols a ``load()`` brought in -- this function
    does not care which, which is why the same three-line ``ast.Name`` branch
    serves all of them.
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else UNEVALUATABLE
    if isinstance(node, ast.Name):
        return bindings.get(node.id, UNEVALUATABLE)
    if isinstance(node, (ast.List, ast.Tuple)):
        out: list = []
        for element in node.elts:
            value = eval_expr(element, bindings)
            if value is UNEVALUATABLE:
                return UNEVALUATABLE
            # Appended as-is, never flattened: a nested list is not a string
            # entry, and splicing one in would invent entries the file does
            # not declare. ``eval_string_list`` drops it.
            out.append(value)
        return out
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = eval_expr(node.left, bindings)
        right = eval_expr(node.right, bindings)
        if isinstance(left, str) and isinstance(right, str):
            return left + right
        if isinstance(left, list) and isinstance(right, list):
            return left + right
        return UNEVALUATABLE
    if isinstance(node, ast.IfExp):
        test = _eval_test(node.test, bindings)
        if test is UNEVALUATABLE:
            return UNEVALUATABLE
        return eval_expr(node.body if test else node.orelse, bindings)
    if isinstance(node, ast.Call) and _is_glob_call(node.func):
        # The one call the evaluator resolves (either spelling), and only
        # when a caller installed a resolver. Everything else -- select(), a
        # macro, any other attribute access -- is still UNEVALUATABLE by
        # design (bd mhn7).
        return _eval_glob(node, bindings)
    return UNEVALUATABLE


def _eval_test(node: ast.AST, bindings: dict):
    """Evaluate a comparison guarding a conditional expression.

    Supports the four operators BUILD files use to switch a ``srcs``/``deps``
    entry on the comprehension variable (``==``, ``!=``, ``in``, ``not in``).
    An unsupported operator makes the whole conditional unevaluatable, which
    drops both branches rather than picking one.
    """
    if not isinstance(node, ast.Compare) or len(node.ops) != 1:
        return UNEVALUATABLE
    left = eval_expr(node.left, bindings)
    right = eval_expr(node.comparators[0], bindings)
    if left is UNEVALUATABLE or right is UNEVALUATABLE:
        return UNEVALUATABLE
    op = node.ops[0]
    if isinstance(op, ast.Eq):
        return left == right
    if isinstance(op, ast.NotEq):
        return left != right
    if isinstance(op, ast.In) and isinstance(right, list):
        return left in right
    if isinstance(op, ast.NotIn) and isinstance(right, list):
        return left not in right
    return UNEVALUATABLE
