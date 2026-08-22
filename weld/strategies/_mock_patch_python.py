"""Harvest ``unittest.mock.patch`` string targets from a Python test module.

A mock patch names its target as a *string literal* --
``patch("weld._mcp_sdk.installed_version")``. That dotted path is a real,
resolvable dependency (the test breaks if the symbol moves) and it is
invisible to the import graph by construction: the test imports nothing from
``weld._mcp_sdk``, so ``python_callgraph`` sees no edge and "who touches this
symbol" silently omits every mock-based test (bd ymso). The measured cost of
that blind spot is bd kj4z, where a mock bound at a re-export site covered one
code path and missed another, and nothing in the graph connected the mock
target to either side.

This module finds the patch calls and emits the edges;
:mod:`weld.strategies._mock_patch_resolve` decides whether a target names a
symbol weld actually knows, and is where the "only proven targets" rule and
its drop cases are documented. :mod:`weld.strategies.test_peer` is the only
caller of either. Both are private (leading underscore) so they do not
register as their own discovery strategies, per the ADR 0046 convention that
governs the per-language ``_test_peer_*`` helpers.

Why ``test_peer`` and not ``python_callgraph``, which already parses every
Python AST: the python trio (``python_module`` / ``python_callgraph`` /
``python_package``) shares one glob whose excludes cover the test tree -- any
divergence is a strategy-pair-consistency violation -- so a test file under
that exclude has no ``symbol:`` nodes at all and ``python_callgraph``
structurally cannot see it. ``test_peer`` walks exactly those files and mints
the ``file:`` node the edge starts from.
"""

from __future__ import annotations

import ast
from pathlib import Path

from weld.strategies._mock_patch_resolve import (
    ModuleCache,
    new_cache,
    resolve_patch_target,
)

#: Edge-side ``props.resolution`` tag for a mock-patch target. Occupies the
#: same slot ``python_callgraph`` uses for call resolution, which is what
#: carries the patch-vs-call distinction without widening
#: ``VALID_EDGE_TYPES`` (ADR 0016: nuance goes in props, the enum stays
#: strict). Filter on it to ask "which dependents are mocks?".
MOCK_PATCH_RESOLUTION = "mock_patch"

#: Attribute/function name of every recognised patch entry point:
#: ``patch(...)``, ``mock.patch(...)`` and ``unittest.mock.patch(...)``.
#: ``patch.object`` / ``patch.dict`` / ``patch.multiple`` are deliberately
#: NOT recognised -- their first argument is an object or a mapping, not the
#: string target this module resolves.
_PATCH_NAME = "patch"


def _is_patch_call(func: ast.expr) -> bool:
    """Return whether *func* is a recognised ``patch`` entry point.

    Matches the bare name and any attribute access ending in ``.patch``, so
    the three idiomatic import styles all resolve. ``patch.object`` and
    friends fall out naturally: their ``attr`` is ``object``, not ``patch``.
    """
    if isinstance(func, ast.Name):
        return func.id == _PATCH_NAME
    if isinstance(func, ast.Attribute):
        return func.attr == _PATCH_NAME
    return False


def _patch_target_literal(call: ast.Call) -> str | None:
    """Return the dotted string target of *call*, or None.

    The target is ``patch``'s first positional parameter. A non-literal
    (an f-string, a variable, a computed name) is not statically resolvable
    and yields None -- weld records what it can prove, not what it can guess.
    """
    if not call.args:
        return None
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def patch_targets(source_text: str, filename: str) -> list[tuple[str, int]]:
    """Return ``(dotted_target, line)`` for every patch call in *source_text*.

    ``ast.walk`` reaches decorator calls (``@patch("...")``), context-manager
    calls (``with patch("..."):``) and bare statement calls alike, because all
    three are :class:`ast.Call` nodes; no separate handling is needed for the
    three ways a patch is spelled. Order follows the walk, which is
    deterministic for a given source.

    The substring pre-check is what keeps this affordable across a whole test
    suite: every recognised spelling contains the identifier ``patch``, so a
    module without it provably has no patch call and never needs parsing. On
    this repo that skips two thirds of the test glob.
    """
    if _PATCH_NAME not in source_text:
        return []
    try:
        tree = ast.parse(source_text, filename=filename)
    except (SyntaxError, ValueError):
        return []
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_patch_call(node.func):
            continue
        dotted = _patch_target_literal(node)
        if dotted is not None:
            found.append((dotted, node.lineno))
    return found


def patch_target_edges(
    root: Path,
    rel: Path,
    from_id: str,
    *,
    cache: ModuleCache,
) -> list[dict]:
    """Return ``depends_on`` edges from *from_id* to each patched symbol.

    *rel* is the repo-relative path of the test module and *from_id* the
    ``file:`` node :mod:`weld.strategies.test_peer` minted for it. The read is
    guarded because a file matched by the walk can be gone by the time a
    strategy reads it -- the per-run glob memo widens that window to the whole
    run (bd pt38) -- and an unreadable test file must cost its mock edges, not
    the discovery run.

    ``depends_on`` is the edge type because it is already one of
    ``impact_core._LOW_CAPABILITY_EDGE_TYPES`` (``calls`` / ``tests`` /
    ``depends_on``), so a mock-only test dependent lands in ``wd impact``'s
    blast radius with no change to the impact engine. ``tests`` would be a
    lie in the other direction: a patched symbol is replaced by the test, not
    exercised by it.

    Per ADR 0074 the provenance stamp is the *test* file -- the file whose
    scan produced the edge -- never the patched module. Stamping the target
    would be exactly backwards: the incremental purge retains an edge across a
    node purge only when it can attribute it to a file, and the target is the
    file that is stale in precisely the case that must not lose the edge.
    """
    try:
        source_text = (root / rel).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    edges: list[dict] = []
    seen: set[str] = set()
    for dotted, line in patch_targets(source_text, str(root / rel)):
        target_id = resolve_patch_target(root, dotted, cache)
        if target_id is None or target_id in seen:
            continue
        seen.add(target_id)
        edges.append(
            {
                "from": from_id,
                "to": target_id,
                "type": "depends_on",
                "props": {
                    "source_strategy": "test_peer",
                    "confidence": "definite",
                    "resolved": True,
                    "raw": dotted,
                    "resolution": MOCK_PATCH_RESOLUTION,
                    "provenance": {"file": rel.as_posix(), "line": line},
                },
            }
        )
    return edges


__all__ = [
    "MOCK_PATCH_RESOLUTION",
    "new_cache",
    "patch_target_edges",
    "patch_targets",
]
