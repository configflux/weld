"""Shared call-site AST-match primitives for the interaction strategies.

The static interaction strategies -- :mod:`weld.strategies.events_callsite`,
:mod:`weld.strategies.events_bindings`, and :mod:`weld.strategies.http_client`
-- walk Python source for calls shaped ``<Root>.<verb>("literal", ...)`` under
ADR 0086's static-truth policy: both the receiver and the string argument must
be structurally clear in the source text, and anything dynamic (assigned
instances, variables, substituting f-strings) is dropped rather than guessed.

Those three strategies previously hand-rolled near-identical blocks -- an
import pre-filter, a literal-string extractor, a receiver/verb unwrap, a
rules-based classifier, a call walk, and a parse-every-source driver. This
module holds the genuinely identical primitives so the strategies share one
implementation. It is deliberately *not* a declarative match DSL: the four
known call shapes do not justify one (that would be speculative generality).

Per the strategy contract, no strategy imports another strategy; shared code
lives in helper modules like this one. Output is unchanged from the
pre-extraction code (ADR 0012 determinism contract): every primitive here is a
straight lift of the logic it replaced, so ``extract`` output stays
byte-identical.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterator

from weld.strategies._helpers import filter_glob_results

#: A classifier rule: ``(receiver_names, verb_names, transport)``. A rule
#: fires when the call's receiver ``Name`` is in ``receiver_names`` and the
#: called attribute is in ``verb_names``, yielding ``transport``.
Rule = tuple[frozenset[str], frozenset[str], str]

def file_imports_root(tree: ast.Module, roots: frozenset[str]) -> bool:
    """Cheap pre-filter: True if *tree* imports a top-level name in *roots*.

    Matches both ``import <root>[...]`` and ``from <root>[...] import ...`` on
    the first dotted segment, so only files that could possibly contain a
    matching call site are AST-walked further.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in roots:
                    return True
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in roots:
                return True
    return False

def literal_string(node: ast.AST) -> str | None:
    """Return the statically-knowable string value of *node*, else None.

    Accepts a plain string constant and a literal-only f-string (a
    ``JoinedStr`` whose parts are all string constants). Any
    ``FormattedValue`` part is a runtime substitution and disqualifies the
    value.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                return None
        return "".join(parts)
    return None

def literal_first_arg(call: ast.Call) -> str | None:
    """Return the literal string first positional arg of *call*, or None."""
    if not call.args:
        return None
    return literal_string(call.args[0])

def literal_str_or_list_arg(call: ast.Call) -> list[str] | None:
    """Return literal strings from a single-string or list first arg, or None.

    Handles the two shapes a ``subscribe``-style call takes: a single
    literal (``sub("topic")``) and a literal list (``sub(["a", "b"])``).
    Any non-literal element -- a variable, a QoS tuple, an f-string with a
    substitution -- drops the whole argument rather than partially guessing
    it (ADR 0086 static-truth policy). Empty-string elements are skipped; a
    result with no surviving element yields ``None``.

    Single-sources the consumer-topic extraction shared by
    :mod:`weld.strategies.events_bindings` (which emits the ``consumes``
    edges) and :mod:`weld.strategies.events_callsite` (which mints the
    channel nodes those edges anchor to), so the node minter and the edge
    emitter can never disagree on which topics a subscribe call declares.
    """
    if not call.args:
        return None
    node = call.args[0]
    # Single string arg (``sub("topic")``).
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value] if node.value else None
    # List arg (``sub(["topic1", "topic2"])``).
    if not isinstance(node, ast.List):
        return None
    result: list[str] = []
    for elt in node.elts:
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
            if elt.value:
                result.append(elt.value)
        else:
            return None  # Any non-literal element kills the list.
    return result or None

def attribute_call_target(call: ast.Call) -> tuple[str, str] | None:
    """Return ``(root, verb)`` for a ``<Name>.<attr>(...)`` call, else None.

    Only direct attribute calls whose receiver is a bare ``Name`` qualify;
    resolving assigned instances or deeper attribute chains is out of scope per
    ADR 0086's static-truth policy.
    """
    func = call.func
    if not isinstance(func, ast.Attribute):
        return None
    if not isinstance(func.value, ast.Name):
        return None
    return func.value.id, func.attr

def classify_receiver_verb(
    call: ast.Call, rules: tuple[Rule, ...]
) -> str | None:
    """Return the transport of the first matching rule in *rules*, or None.

    A rule ``(receiver_names, verb_names, transport)`` fires when the call's
    receiver ``Name`` is in ``receiver_names`` and the called attribute is in
    ``verb_names``.
    """
    target = attribute_call_target(call)
    if target is None:
        return None
    root, verb = target
    for roots, verbs, transport in rules:
        if root in roots and verb in verbs:
            return transport
    return None

def iter_call_nodes(tree: ast.Module) -> Iterator[ast.Call]:
    """Yield every ``ast.Call`` node in *tree*, in ``ast.walk`` order."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            yield node

def iter_python_asts(
    root: Path, pattern: str, *, skip_underscore: bool = False
) -> Iterator[tuple[str, ast.Module]]:
    """Yield ``(rel_path, tree)`` for each parseable ``.py`` file under *pattern*.

    Expands *pattern* against *root* (``Path.glob`` handles both ``src/*.py``
    and ``src/**/*.py``), applies the repo-boundary filter, then skips
    non-files, non-``.py`` suffixes, unreadable files (``OSError``), and
    unparseable files (``SyntaxError``). When *skip_underscore* is set,
    ``_``-prefixed filenames are skipped too. The caller applies its own
    import pre-filter to the yielded tree.
    """
    for py in filter_glob_results(root, sorted(root.glob(pattern))):
        if not py.is_file() or py.suffix != ".py":
            continue
        if skip_underscore and py.name.startswith("_"):
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            tree = ast.parse(text, filename=str(py))
        except SyntaxError:
            continue
        yield str(py.relative_to(root)), tree
