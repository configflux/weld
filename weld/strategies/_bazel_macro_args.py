"""Parameterized macro call binding (ADR 0109 amendment, ADR 0123).

ADR 0109 measured every macro definition and every macro call site in this
repo at zero parameters / zero arguments, and left both cases unevaluated on
that basis: a macro that takes parameters is not recognized as one this
evaluator can expand, and a call that passes arguments to a macro it *does*
recognize (the zero-parameter kind) still yields nothing. That measurement
changed: ``weld/tests/bench/bench_py_test.bzl`` declares
``def bench_py_test(name, tags = [], local = True, **kwargs)``, called 22
times with keyword arguments, and every one of those calls was invisible.

This module adds the one thing :mod:`weld.strategies._bazel_loads` and
:mod:`weld.strategies._bazel_starlark` deliberately did not need before:
mapping a call site's positional and keyword arguments onto a macro
definition's parameters. It is still not a Starlark interpreter -- it
resolves exactly one thing, which literal value reaches which name at the
point :func:`weld.strategies._bazel_starlark.targets_in` walks the body --
and it keeps the same asymmetry every other construct in this evaluator
already has: a call shape outside the supported subset declines to bind
(returns ``None``) rather than guess, so that one call site contributes no
targets rather than a wrong one.

Two AST finders partition every macro definition in the repo by parameter
shape, disjoint from :func:`weld.strategies._bazel_loads.macro_defs`'s
existing zero-parameter bucket:

* :func:`param_macro_defs` -- defs with one or more positional-or-keyword
  parameters and/or a ``**kwargs``, so long as they use no ``*args``,
  positional-only, or keyword-only parameters. Those three shapes get the
  same "outside the subset" treatment ADR 0109 already gives
  ``glob()``/``select()``: never recognized, never expanded.
* :func:`param_macro_calls` -- every module-level call site naming a
  ``param_macro_defs`` entry, any arity, one entry **per call site**, not
  deduplicated by name. Unlike a zero-argument macro call (where two calls
  to the same macro produce byte-identical output), two calls to a
  parameterized macro almost always carry different arguments and must each
  be expanded on their own.

:func:`bind_macro_call` is the binder itself. What it declines to bind is
listed on its own docstring; what it does not attempt at all -- executing
the macro body's own statements -- is ADR 0123's documented boundary: a rule
call that reads a parameter *after* the body reassigns it (as
``bench_py_test``'s ``tags = tags + ["no-sandbox"]`` does) sees the
originally-bound value, never the reassigned one. That costs nothing here
because ``tags`` is not one of the label attributes
(``weld.strategies._bazel_starlark.LABEL_ATTRS``) any target dict reads.
"""

from __future__ import annotations

import ast
from typing import Container

from weld.strategies._bazel_eval import eval_expr


def param_macro_defs(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    """Return module-level ``def``s with a supported non-zero-parameter signature.

    Supported: one or more positional-or-keyword parameters, each optionally
    defaulted, plus an optional trailing ``**kwargs``. Not supported --
    excluded from the result entirely, exactly as a zero-parameter def would
    exclude a parameterized one from
    :func:`weld.strategies._bazel_loads.macro_defs` -- is any use of
    ``*args``, positional-only parameters (``def f(a, /):``), or
    keyword-only parameters (``def f(*, a):``): none of those bind by a
    simple index-or-name rule, and guessing at one risks binding the wrong
    call-site value to the wrong name, which is the one failure mode this
    evaluator exists to avoid.

    A def with *zero* parameters and no ``**kwargs`` is excluded too --
    that shape already belongs to
    :func:`weld.strategies._bazel_loads.macro_defs`, and admitting it here
    as well would let the same call site be expanded twice, once by each
    bucket. The two dicts are meant to partition every def by shape, not
    overlap.
    """
    out: dict[str, ast.FunctionDef] = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        args = node.args
        if args.posonlyargs or args.kwonlyargs or args.vararg:
            continue
        if not args.args and not args.kwarg:
            continue
        out[node.name] = node
    return out


def param_macro_calls(
    tree: ast.Module, names: Container[str]
) -> list[tuple[str, ast.Call]]:
    """Return every module-level call site naming a macro in *names*.

    Order-preserving, one entry per call site. Deliberately **not**
    deduplicated by name the way
    :func:`weld.strategies._bazel_loads.zero_arg_calls` is: a zero-argument
    call to the same macro always produces the same targets, so expanding it
    once is correct and cheap, but two calls to a parameterized macro carry
    different arguments (a different ``name=`` at minimum, or Bazel would
    reject the second declaration) and must each be expanded on their own.
    Any arity is returned, including zero -- a parameterized macro whose
    every parameter has a default can legally be called with no arguments at
    all, and :func:`bind_macro_call` already resolves that case correctly
    from defaults alone.
    """
    out: list[tuple[str, ast.Call]] = []
    for node in tree.body:
        call = node.value if isinstance(node, ast.Expr) else None
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
            if call.func.id in names:
                out.append((call.func.id, call))
    return out


def bind_macro_call(
    func_def: ast.FunctionDef,
    call: ast.Call,
    caller_env: dict,
    def_env: dict,
) -> dict | None:
    """Bind *call*'s arguments onto *func_def*'s parameters, or decline.

    Returns a namespace for evaluating *func_def*'s body: *def_env* (the
    defining module's own bindings -- the ``.bzl``'s globals, matching what
    :func:`weld.strategies._bazel_loads.Macro.bindings` already carries for
    the zero-parameter case) overlaid with the call's bound parameter
    values, which shadow a same-named global exactly as a real parameter
    would. Positional arguments are evaluated against *caller_env* (the
    calling BUILD file's own scope -- where the call expression's argument
    expressions are written); default expressions are evaluated against
    *def_env* (the defining module's scope, matching Starlark's own
    def-time-evaluation semantics for defaults).

    Returns ``None`` -- decline to bind at all -- for any call or signature
    shape this function does not resolve unambiguously:

    * a positional argument uses ``*`` unpacking at the call site
      (``ast.Starred``);
    * a keyword argument uses ``**`` unpacking at the call site
      (``keyword.arg is None`` -- a *call-site* ``**kwargs``, the mirror
      image of the *definition-site* ``**kwargs`` this function does
      support);
    * more positional arguments are given than *func_def* declares (there is
      no ``*args`` to catch the overflow -- :func:`param_macro_defs` already
      excludes defs that have one);
    * a keyword argument is bound twice (once positionally, once by the same
      name);
    * a keyword argument names neither a parameter nor -- when *func_def*
      declares no ``**kwargs`` -- has anywhere to go;
    * a parameter with no default is never bound by the call.

    Every one of these is a call weld cannot resolve without guessing, so
    the caller treats ``None`` as "this call site contributes no targets" --
    the same outcome an unevaluatable ``glob()`` or ``select()`` already
    produces elsewhere in this evaluator, never a wrong one.

    *func_def* itself is re-checked for the same shape
    :func:`param_macro_defs` already filters on
    (``*args``/positional-only/keyword-only all decline) rather than trusted
    from the caller: this function's whole reason to exist is never binding a
    call-site value to the wrong parameter, and a second, independent check
    here costs three lines against a caller someday passing an unfiltered
    def -- belt-and-braces containment, the same shape ADR 0109's own label
    resolver uses (``resolve_bzl_label`` *and* ``_bzl_reader`` each reject an
    escaping path independently).
    """
    args = func_def.args
    if args.posonlyargs or args.kwonlyargs or args.vararg:
        return None
    params = [a.arg for a in args.args]
    required = len(params) - len(args.defaults)
    defaults = {
        params[required + i]: default for i, default in enumerate(args.defaults)
    }
    kwarg_name = args.kwarg.arg if args.kwarg else None

    if any(isinstance(a, ast.Starred) for a in call.args):
        return None
    if any(keyword.arg is None for keyword in call.keywords):
        return None
    if len(call.args) > len(params):
        return None

    bound: dict = {}
    extra: dict = {}
    used: set[str] = set()

    for position, value_node in enumerate(call.args):
        pname = params[position]
        bound[pname] = eval_expr(value_node, caller_env)
        used.add(pname)

    for keyword in call.keywords:
        name = keyword.arg
        if name in params:
            if name in used:
                return None
            bound[name] = eval_expr(keyword.value, caller_env)
            used.add(name)
        elif kwarg_name is not None:
            extra[name] = eval_expr(keyword.value, caller_env)
        else:
            return None

    for pname in params:
        if pname in used:
            continue
        if pname not in defaults:
            return None
        bound[pname] = eval_expr(defaults[pname], def_env)

    if kwarg_name is not None:
        bound[kwarg_name] = extra

    return {**def_env, **bound}
