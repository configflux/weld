"""Starlark-aware BUILD file parsing for the ``bazel`` strategy (ADR 0105).

ADR 0044 shipped a line-oriented regex scanner: a rule was a line whose first
token was the rule name, and its ``name`` kwarg was a quoted literal on a line
of its own. That reads the *formatting* of a declaration rather than its
grammar, and this repo declares the bulk of its own suite the other legal way::

    [py_test(name = _n, srcs = [_n + ".py"], deps = [...]) for _n in ("a", "b")]

The line starts with ``[`` so the rule regex missed, and the name is ``_n`` so
the name regex missed. 115 ``py_test`` declarations in ``weld/tests`` produced
33 nodes, and "which Bazel target runs this file" was unanswerable for most of
the suite (bd s3pq).

Starlark is a Python subset for the constructs that appear in rule
declarations, so :mod:`ast` accepts real BUILD files and hands this module the
actual grammar. What it cannot evaluate it **drops**: a target whose ``name``
does not resolve to a string is not emitted at all, and an unevaluatable
``srcs``/``deps`` contributes no entries rather than a guess. That is the
``_target_ids`` lesson restated -- a wrong-but-real target ID is worse than a
missing one, because nothing downstream can tell it from a real one. ADR 0043's
"deterministic > sound" posture is unchanged.

ADR 0044's remaining limit was ``load()``: the parser had no handling for it at
all, so a BUILD file's loaded names were simply unbound. That one absence was
three separate reported gaps (bd 73xa, bd akwh, bd rh3l), because a load brings
in two kinds of thing and this repo uses both -- a *constant* whose value is a
target's real ``srcs`` list (``weld/runtime_srcs.bzl``), and a *macro* whose
body declares real targets in the calling package (``weld/tests/*_tests.bzl``,
208 of that package's py_tests). :mod:`weld.strategies._bazel_loads` resolves
the label and reads the file; this module supplies the three evaluator pieces
that need the grammar: :func:`module_bindings` (a sequential fold over
module-level assignments) and :func:`targets_in`, which walks a *scope* rather
than a whole tree so an uncalled macro's body declares nothing.

``select(...)`` remains opaque. ``glob(...)`` -- bare, or ``native.``-prefixed
inside a macro body -- resolves when a caller installs the bounded resolver
(bd mhn7, bd x9lg); a macro that takes parameters, and a call that passes
arguments, resolve too (ADR 0123).
"""

from __future__ import annotations

import ast
from collections import deque
from typing import Container, Iterator

from weld.strategies._bazel_eval import (
    UNEVALUATABLE,
    eval_expr,
    eval_kwarg,
    eval_string_list,
)

#: Rule attributes read as label lists. ``data`` joined ``srcs``/``deps`` in
#: bd oj3m: 130 rule calls in this repo declare it and every one was dark, so
#: "which tests execute against ``examples/``" was unanswerable even though
#: ``py_test(data = ["//examples:example_files"])`` says exactly that. See
#: ADR 0044 § "Amendment: ``load()``" for why that measurement retired the
#: proposed ``runs_against`` edge type rather than motivating it.
LABEL_ATTRS = ("srcs", "deps", "data")


def parse_module(text: str) -> ast.Module | None:
    """Parse *text* as Starlark, or return ``None`` if it will not parse.

    ``None`` is distinct from an empty module: a file weld could not parse is a
    *failure* the caller must record (bd hch4), while a file that parsed and
    declared nothing is a decision. Collapsing the two writes a failure into
    the exemption set that keeps the per-file repair from ever re-running it.
    """
    try:
        return ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return None


def parse_targets(
    text: str,
    rules: Container[str],
    env: dict | None = None,
) -> list[dict] | None:
    """Return target dicts declared in *text*, or ``None`` if it will not parse.

    Each dict is ``{"rule", "name", "srcs", "deps", "data"}``, matching the
    shape the ``bazel`` strategy consumed from the previous regex parser plus
    the ``data`` attribute bd oj3m measured as the missing one.

    *env* seeds the module namespace with names bound elsewhere -- the symbols
    a ``load()`` brought in. Absent it, this is the ADR 0105 behaviour
    unchanged.
    """
    tree = parse_module(text)
    if tree is None:
        return None
    bindings = module_bindings(tree, env or {})
    return targets_in(tree, rules, bindings)


def targets_in(
    node: ast.AST,
    rules: Container[str],
    env: dict | None = None,
    origins: dict[str, str] | None = None,
) -> list[dict] | None:
    """Return target dicts declared directly by *node*, or ``None`` on overflow.

    *node* is either a module (a BUILD file) or a ``FunctionDef`` (a macro body
    being expanded into its calling package). Rule calls nested inside a
    ``def`` are **not** collected when walking a module: a macro that is never
    called declares no targets, and emitting its body's rules anyway would
    invent targets bazel does not have -- the asymmetry bd akwh pinned.

    *origins* maps a loaded symbol name to the ``.bzl`` that defined it. Each
    target reports, in ``bzl``, the files its own declaration actually read a
    name from -- not every file its package happens to load. See
    :func:`_referenced_origins`.
    """
    # The walk and the evaluator recurse with the expression tree's depth, so
    # a pathologically nested BUILD file can exhaust the stack *after* it has
    # parsed. Reported as a failure rather than allowed to propagate: this
    # strategy runs mid-orchestration over every BUILD file in the repo, and
    # one such file taking the whole discovery run down is the pt38 failure
    # shape -- an unhandled error does not stay local, it just surfaces
    # somewhere less obviously connected to its cause.
    try:
        targets: list[dict] = []
        for call, bindings in _iter_rule_calls(node, rules, env or {}):
            target = _target_from_call(call, bindings)
            if target is not None:
                target["bzl"] = _referenced_origins(call, origins or {})
                targets.append(target)
    except RecursionError:
        return None
    return targets


def _referenced_origins(call: ast.Call, origins: dict[str, str]) -> list[str]:
    """Return the ``.bzl`` paths whose symbols *call* actually names.

    Precision is the point. Bazel reanalyzes every target in a package when any
    ``.bzl`` that package loads changes, so "one edge per target in the loading
    BUILD file" would be defensible -- and useless: ``weld/tests`` loads 16
    ``.bzl`` files into ~600 targets, which is ~9,600 edges saying almost
    nothing, and it would bury the 15 targets a given macro really declares
    under the 600 it does not. That is ADR 0107's lesson restated: a coarse
    true edge that drowns the precise one is not a better answer.

    So the edge is emitted only where the declaration *reads* the file: a
    target whose ``srcs`` is ``RUNTIME_SRCS`` names ``runtime_srcs.bzl``, and
    its package-mates that do not mention it name nothing.
    """
    found = {
        origins[node.id]
        for node in ast.walk(call)
        if isinstance(node, ast.Name) and node.id in origins
    }
    return sorted(found)


def module_bindings(tree: ast.Module, env: dict) -> dict:
    """Fold module-level ``NAME = <expr>`` assignments over *env*.

    Sequential, because Starlark is: ``_ALL = _A + _B`` must see the two names
    bound above it. An assignment whose value will not evaluate binds nothing
    rather than binding a guess, so a later reference to it is unevaluatable
    too and costs only its own entries.

    BUILD files bind constants of their own (``tools/release_claims`` declares
    ``RELEASE_CLAIMS_SRCS`` then spends it on two targets' ``srcs``), so this
    closes a pre-existing miss as well as carrying loaded symbols.
    """
    bindings = dict(env)
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        try:
            value = eval_expr(node.value, bindings)
        except RecursionError:
            continue
        if value is not UNEVALUATABLE:
            bindings[target.id] = value
    return bindings


def _iter_rule_calls(
    tree: ast.AST,
    rules: Container[str],
    env: dict,
) -> Iterator[tuple[ast.Call, dict]]:
    """Yield each rule call paired with the variable bindings it evaluates under.

    Comprehensions are resolved first so their inner calls are not also yielded
    bare: the same call yielded twice would emit the target once per binding
    *and* once with the loop variable unbound.
    """
    handled: set[int] = set()
    comprehensions = [
        node
        for node in _walk_scope(tree)
        if isinstance(node, (ast.ListComp, ast.GeneratorExp, ast.SetComp))
    ]

    # A comprehension nested inside another is not evaluated: its bindings
    # depend on the outer loop, so resolving it in isolation would bind the
    # inner variable and silently drop the outer one.
    nested: set[int] = set()
    for comp in comprehensions:
        for inner in ast.walk(comp.elt):
            if isinstance(inner, (ast.ListComp, ast.GeneratorExp, ast.SetComp)):
                nested.add(id(inner))

    for comp in comprehensions:
        calls = [
            node
            for node in ast.walk(comp.elt)
            if isinstance(node, ast.Call) and _rule_name(node, rules)
        ]
        for call in calls:
            handled.add(id(call))
        if id(comp) in nested:
            continue
        bindings = _comprehension_bindings(comp, env)
        if bindings is None:
            continue
        for binding in bindings:
            for call in calls:
                yield call, binding

    for node in _walk_scope(tree):
        if (
            isinstance(node, ast.Call)
            and _rule_name(node, rules)
            and id(node) not in handled
        ):
            yield node, env


def _walk_scope(root: ast.AST) -> Iterator[ast.AST]:
    """Breadth-first walk of *root* that never enters a nested ``def``.

    :func:`ast.walk` minus function bodies. A macro's rule calls belong to
    whatever package *calls* the macro, so they must be reached by expanding a
    call site -- reaching them by walking the definition would emit targets for
    a macro nobody invokes. Breadth-first to match :func:`ast.walk`, because
    emission order is the graph's determinism.
    """
    queue: deque[ast.AST] = deque([root])
    while queue:
        node = queue.popleft()
        yield node
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef):
                continue
            queue.append(child)


def _rule_name(call: ast.Call, rules: Container[str]) -> str | None:
    """Return the rule name if *call* invokes a known rule.

    Accepts the bare name (``py_test(...)``, how BUILD files spell it) and the
    ``native.`` prefix (``native.filegroup(...)``, how a ``.bzl`` macro body
    must spell it). Inside a ``.bzl`` the two are the same rule, so declining
    the prefixed form would drop real targets -- and this repo's macro bodies
    use it (bd akwh).
    """
    func = call.func
    if isinstance(func, ast.Name) and func.id in rules:
        return func.id
    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "native"
        and func.attr in rules
    ):
        return func.attr
    return None


def _comprehension_bindings(
    comp: ast.ListComp | ast.GeneratorExp | ast.SetComp,
    env: dict,
) -> list[dict] | None:
    """Return one binding per iteration, or ``None`` if not statically known.

    Two loop-variable shapes are accepted, and both must be exact: a bare name
    over a sequence of strings (``for _n in ("a", "b")``), and positional
    unpacking over a sequence of equal-length rows
    (``for _name, _deps in ((("a"), [...]), ...)``). The second is how this
    repo declares its agent-graph suite, where the row carries a name *and* a
    deps list -- so a binding value is not always a string, which is why
    :func:`weld.strategies._bazel_eval.eval_expr` binds whatever it evaluated
    rather than strings only.

    Everything else -- a nested loop, a filtered loop, multiple ``for``
    clauses, a row whose arity does not match the target, an iterable built by
    a call -- is unevaluatable, and an unevaluatable comprehension yields no
    targets rather than partially-bound ones.
    """
    if len(comp.generators) != 1:
        return None
    generator = comp.generators[0]
    if generator.ifs or getattr(generator, "is_async", 0):
        return None
    # Evaluated under *env* so a macro body's ``for _n in _TUPLE`` sees the
    # module-level tuple its own ``.bzl`` declared above it.
    values = eval_expr(generator.iter, env)
    if not isinstance(values, list) or not values:
        return None

    if isinstance(generator.target, ast.Name):
        if not all(isinstance(v, str) for v in values):
            return None
        return [{**env, generator.target.id: value} for value in values]

    if isinstance(generator.target, ast.Tuple):
        names = [e.id for e in generator.target.elts if isinstance(e, ast.Name)]
        if len(names) != len(generator.target.elts):
            return None
        rows = []
        for value in values:
            if not isinstance(value, list) or len(value) != len(names):
                return None
            rows.append({**env, **dict(zip(names, value))})
        return rows

    return None


def _target_from_call(call: ast.Call, bindings: dict) -> dict | None:
    """Build a target dict from *call*, or ``None`` when its name is unknown.

    The name is identity: without it there is no node ID to mint, so the
    target is dropped whole. The label attributes are edges, so an
    unevaluatable one costs only its own entries -- the node is still real and
    still worth emitting.
    """
    rule = _call_rule(call)
    if rule is None:
        return None
    name = eval_kwarg(call, "name", bindings)
    if not isinstance(name, str) or not name:
        return None
    target = {"rule": rule, "name": name}
    for attr in LABEL_ATTRS:
        target[attr] = eval_string_list(call, attr, bindings)
    return target


def _call_rule(call: ast.Call) -> str | None:
    """Return the spelled rule name of *call* (bare or ``native.``-prefixed)."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return func.attr if func.value.id == "native" else None
    return None
