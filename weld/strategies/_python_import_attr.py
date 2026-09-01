"""The one channel a per-glob strategy has to a rule that sees the whole graph.

``python_callgraph`` resolves ``<name>.<attr>()`` against the *calling* module's
import table. When the table says ``from PARENT import CHILD``, the strategy has
to decide whether ``CHILD`` names a submodule (so the call resolves under
``PARENT.CHILD``) or an ordinary value (so ``attr`` is a method on whatever the
name holds, and nothing in ``PARENT`` can claim it). Both readings are legal
Python; telling them apart needs to know which dotted paths are real modules and
what they define -- a global view a strategy walking one glob at a time does not
have.

So the strategy answers what it can prove from its own glob and, when it cannot,
records the facts on the edge and leaves the sentinel standing:
``props.import_attr`` = the parent module, the imported base name, and the
attribute. :mod:`weld._graph_closure_import_attr` reads it back inside
``close_graph``, which runs once per discover over the whole merged node/edge
set on both the full and the incremental path.

The hint stays on the edge after the rule has run. It is not spent bookkeeping:
an incremental round does not re-walk a clean caller, so the hint is the only
thing that lets a retained edge be restored and re-derived against a graph that
has since changed -- the same reason ``_link_imports`` re-derives from a retained
``props.imports_from`` rather than from what some earlier round concluded.

Which is also why it is read back defensively. These props come off
``.weld/graph.json``, a plain file on disk that a previous version of weld (or a
hand edit) may have written; :func:`read_import_attr_hint` answers ``None`` for
anything it cannot vouch for, and the pass degrades to "no rule applies", never
to a node id assembled from an unvalidated string.
"""

from __future__ import annotations

from typing import Any, NamedTuple

#: Edge-prop key carrying the hint. The key IS the resolution kind -- there is
#: exactly one shape a strategy defers this way -- so no separate kind field.
IMPORT_ATTR_PROP = "import_attr"

#: Endpoints a deferred target may occupy. A ``calls`` edge points AT its
#: target; a ``decorates`` edge points FROM it (the decorator applies to the
#: decorated symbol, ADR 0122). Recorded rather than derived from the edge
#: type, so the closure never has to hold a second copy of each emitter's
#: endpoint layout -- the emitter that builds the edge is the one place that
#: knows, and a new deferring edge kind says so itself.
SIDES = ("from", "to")

_MODULE = "module"
_BASE = "base"
_ATTR = "attr"
_SIDE = "side"


class ImportAttrHint(NamedTuple):
    """``from <module> import <base>`` followed by ``<base>.<attr>``.

    ``module`` and ``base`` come from the import table's two slots; ``attr`` is
    the attribute the call named. All three are needed by a closure rule: the
    submodule reading asks about ``module.base``, the class-base reading asks
    about ``module``'s own ``base`` symbol, and both land on ``attr``.
    """

    module: str
    base: str
    attr: str
    side: str

    @property
    def submodule(self) -> str:
        """The dotted path ``base`` would name if it were a submodule."""
        return f"{self.module}.{self.base}"


def make_import_attr_hint(module: str, base: str, attr: str) -> dict[str, str]:
    """Record what a deferred attribute call turned on, minus its endpoint.

    The resolver answers an expression, not an edge, so it cannot know which
    endpoint the target will occupy; :func:`import_attr_props` completes the
    payload at emission time.
    """
    return {_MODULE: module, _BASE: base, _ATTR: attr}


def import_attr_props(hint: dict[str, str], side: str) -> dict[str, str]:
    """Complete *hint* for an edge whose deferred target sits on *side*."""
    return {**hint, _SIDE: side}


def read_import_attr_hint(props: Any) -> ImportAttrHint | None:
    """Return the hint *props* carries, or ``None`` if it carries none it can vouch for.

    Every field must be a non-empty string, and each must be the shape
    ``_build_import_table`` can actually produce: ``base`` and ``attr`` a single
    identifier, ``module`` a dotted path of them, ``side`` a real endpoint. Not
    tidiness -- these three strings are concatenated into a node id and the
    fourth decides which end of an edge is rewritten, so a value that never came
    from an AST is a value that gets to name something. Bounding them to
    identifiers keeps a crafted or corrupted graph unable to mint an id of a
    shape the strategy could not have minted itself.
    """
    if not isinstance(props, dict):
        return None
    raw = props.get(IMPORT_ATTR_PROP)
    if not isinstance(raw, dict):
        return None
    values: list[str] = []
    for key in (_MODULE, _BASE, _ATTR, _SIDE):
        value = raw.get(key)
        if not isinstance(value, str) or not value:
            return None
        values.append(value)
    module, base, attr, side = values
    if side not in SIDES:
        return None
    if not (base.isidentifier() and attr.isidentifier()):
        return None
    if not all(part.isidentifier() for part in module.split(".")):
        return None
    return ImportAttrHint(module=module, base=base, attr=attr, side=side)


__all__ = [
    "IMPORT_ATTR_PROP",
    "SIDES",
    "ImportAttrHint",
    "import_attr_props",
    "make_import_attr_hint",
    "read_import_attr_hint",
]
