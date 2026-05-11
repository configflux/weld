"""Scope-local variable expansion for ``cpp_cmake`` (ADR 0057).

CMake's variable model is intentionally rich (cache, parent-scope,
function/macro shadowing, generator expressions). This helper implements
the slice ADR 0057 mandates: ``set(VAR value...)`` and ``${VAR}``
substitution *within a single CMakeLists.txt file*, mirroring ADR 0044's
"deterministic but not sound" posture.

Out of scope (kept as raw labels via ``unresolved_labels`` on the
emitted node):

- ``function``/``macro``-scope shadowing of caller variables.
- ``set(VAR value PARENT_SCOPE)`` -- still applied to local scope here.
- Generator expressions ``$<...>``, ``$ENV{...}``, ``$CACHE{...}``.
- ``list(APPEND ...)`` and friends.
- Conditional ``if/endif`` blocks (we expand variables regardless of
  the condition value -- the caller decides whether to use the result).

The API is two pure functions:

- :func:`apply_set` records a ``set(VAR ...)`` definition into a scope
  dict.
- :func:`expand` substitutes ``${VAR}`` occurrences from the scope
  dict, leaving unresolved references untouched.

Both are total over their inputs and never raise.
"""

from __future__ import annotations

import re

__all__ = [
    "apply_set",
    "contains_unresolved",
    "expand",
    "is_resolvable",
]

# ``${VAR}`` substitution pattern. CMake allows nested ``${...}`` and
# generator expressions ``$<...>``; we expand one level here and leave
# nested or generator forms intact so the caller can flag them as
# unresolved.
_VAR_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

# Generator-expression marker (``$<...>``) and ``$ENV{...}`` / ``$CACHE{...}``
# forms. Strings containing these are considered unresolvable in v1.
_GENEX_RE = re.compile(r"\$<|\$ENV\{|\$CACHE\{")


def apply_set(args: list[str], scope: dict[str, str]) -> None:
    """Record a ``set(VAR value [value ...])`` into *scope*.

    Behaviour:

    - ``set(VAR)``                  -> deletes ``VAR`` from *scope*.
    - ``set(VAR value)``            -> stores the single value.
    - ``set(VAR a b c)``            -> stores ``"a;b;c"`` (CMake list).
    - ``set(VAR a b PARENT_SCOPE)`` -> stores ``"a;b"`` locally; the
      PARENT_SCOPE keyword is dropped (file-scope is the only scope
      v1 tracks).
    - ``set(VAR a CACHE STRING ...)`` -> stores ``"a"``; CACHE and the
      following type/docstring/FORCE tokens are dropped.

    Unsupported call shapes (no VAR token, etc.) are ignored.
    """
    if not args:
        return
    var = args[0]
    if not var:
        return

    rest = list(args[1:])
    # Drop the trailing CMake keywords we do not track in v1.
    # CACHE consumes "CACHE <TYPE> <DOCSTRING>" plus optional FORCE.
    if "CACHE" in rest:
        idx = rest.index("CACHE")
        # Take the tokens before CACHE as the value list.
        rest = rest[:idx]
    if rest and rest[-1] == "PARENT_SCOPE":
        rest = rest[:-1]
    if rest and rest[-1] == "FORCE":
        rest = rest[:-1]

    if not rest:
        scope.pop(var, None)
        return

    # CMake joins multiple value tokens with ``;`` to form a list. Some
    # callers pass a single token; either form round-trips correctly.
    scope[var] = ";".join(rest)


def is_resolvable(value: str) -> bool:
    """Return ``False`` if *value* contains generator-expressions or
    ENV/CACHE refs that this v1 expander does not handle."""
    return _GENEX_RE.search(value) is None


def contains_unresolved(value: str, scope: dict[str, str]) -> bool:
    """Return ``True`` if *value* references a ``${VAR}`` not in *scope*."""
    for match in _VAR_REF_RE.finditer(value):
        if match.group(1) not in scope:
            return True
    return False


def expand(value: str, scope: dict[str, str]) -> str:
    """Return *value* with ``${VAR}`` references substituted from *scope*.

    Unknown variables are left untouched (``${MISSING}`` stays
    ``${MISSING}`` in the output) so the caller can detect and flag
    them as ``unresolved_labels``.

    The expansion runs to a fixed point with a small iteration cap so a
    self-referential definition (``set(A "${A}")``) cannot loop
    forever; once no substitution occurs the result is returned as-is.
    """
    if not value:
        return value
    current = value
    for _ in range(8):
        def _sub(match: re.Match[str]) -> str:
            name = match.group(1)
            return scope.get(name, match.group(0))

        next_value = _VAR_REF_RE.sub(_sub, current)
        if next_value == current:
            return next_value
        current = next_value
    return current
