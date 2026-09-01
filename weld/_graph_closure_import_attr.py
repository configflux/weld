"""Decide, on the merged graph, what an imported name's attribute call meant.

``python_callgraph`` resolves ``<name>.<attr>()`` against the *calling* module's
import table. When the table says ``from PARENT import CHILD``, three readings
are legal Python and the strategy cannot always tell them apart:

* ``CHILD`` is a **submodule** -- ``from lib import inner``, ``inner.work()``
  means ``lib.inner.work``.
* ``CHILD`` is a **class** -- ``from weld.bm25 import BM25Corpus``,
  ``BM25Corpus.from_nodes()`` means ``BM25Corpus``'s own ``from_nodes``, a
  symbol the walk already emitted under its dotted qualname.
* ``CHILD`` is an ordinary **value** -- ``from tables import TABLE``,
  ``TABLE.get()`` means a method on whatever ``TABLE`` holds, which is a
  sibling of nothing.

The strategy answers the first reading only for a module its *own glob* owns,
and hands everything else here with the two names the reading turns on
(``props.import_attr``, see :mod:`weld.strategies._python_import_attr`). This
pass runs inside ``close_graph``, which ``weld._discover_postprocess`` calls
once per discover over the whole merged node/edge set -- the only place with
the global view, and the same place on both the full and the incremental path.

Why the strategy stopped deciding it
------------------------------------
It used to decide from ``project_modules``, the set it also tags origins with.
ADR 0074 derives that set two ways on purpose: this glob's own modules on a
full discover, and the union across every glob (read off the post-purge prior
nodes) on an incremental one, so a dirty file in one glob can still tag a call
into another as ``origin=project``. Keying a *resolution* on it meant a caller
in glob A calling into glob B resolved ``symbol:py:lib.inner:work`` after an
incremental refresh and fell to ``symbol:unresolved:work`` on a full discover of
the same tree -- not a recall difference but an equivalence violation, an
incremental graph a full run never produces. Membership of one glob is the same
question on both paths, so that is what the strategy keeps; the wider question
is asked once, here.

What is refused
---------------
Each rule admits a retarget only on evidence the merged graph already holds,
and the sentinel stands whenever neither finds it.

The submodule rule needs the dotted path ``PARENT.CHILD`` to have a Python
``file:`` node in this graph -- the closure's standing test for "first-party
module", shared with the re-export walk through
:func:`weld._graph_closure_modules.python_module_index`. The one first-party
module shape with no file node is an empty ``__init__.py``, which
``python_module`` deliberately does not anchor -- and a package that defines
nothing has no member for the call to land on either, so the sentinel is the
true answer there rather than a missed one.

The class-base rule needs *two* walked symbols: ``PARENT``'s own ``CHILD`` as a
definite ``kind=class``, and ``CHILD.<attr>`` under it. One check is not enough
in either direction. Measured on this repo, 32 of the 78 hinted edges name a
base that is a module-level constant -- a dict, a compiled regex, a message
template -- so a rule keyed on the base alone would fabricate more ids than the
deferral removed; and requiring both to be ``definite`` is what stops a
speculative stub minted by one pass from justifying the next one's retarget.
An inherited method is refused for the same reason it is not walked: ``Sub``
emits no ``Sub.method`` symbol when ``Base`` defines it.

So a stdlib value import (``from pathlib import Path`` + ``Path.cwd()``), a
third-party one, a first-party name that is genuinely a value, and a real class
called on a member it does not define all keep the sentinel.

Why it undoes itself first
--------------------------
Same reason :mod:`weld._graph_closure_reexport_edges` does, and the same
discipline: this pass moves an endpoint on a *retained* edge, and an incremental
round does not re-walk a clean caller, so a move made in an earlier round would
otherwise be inherited no matter what happened to the module since. Deleting
``lib/inner.py`` must degrade both paths to the sentinel, and on the incremental
path nothing dangles to force a re-walk -- the retarget may have landed on a
still-present stub, or the re-export walk may have carried the endpoint on to a
definition in a third module that is still there.

Unlike the re-export retarget, this one needs no bookkeeping key to undo: the
endpoint it replaced is ``symbol:unresolved:<attr>``, a pure function of the
hint the edge still carries. So every round restores that endpoint, drops any
re-export bookkeeping recorded on top of a move this pass made (that chain
started here and is void once the endpoint moves), and re-derives from scratch
-- which makes the whole pass a function of the persisted hint plus the current
graph.
"""

from __future__ import annotations

from typing import Callable, NamedTuple

from weld._graph_closure_modules import python_module_index
from weld._graph_closure_reexport_edges import (
    STUB_PROPS,
    SYMBOL_PREFIX,
    collapse_collisions,
)
from weld.strategies._python_import_attr import (
    IMPORT_ATTR_PROP,
    ImportAttrHint,
    read_import_attr_hint,
)
from weld.strategies._python_origin import (
    is_builtin_name,
    make_resolved_target_node,
    make_sentinel_node,
    origin_for_sentinel,
)

_UNRESOLVED_PREFIX = "symbol:unresolved:"


class ImportAttrTarget(NamedTuple):
    """Where a rule says a deferred attribute call actually lands.

    ``resolution`` and ``origin`` travel with the node id because a rule knows
    *why* it resolved and the pass does not: the edge's ``props.resolution``
    and, when the target has to be minted, the node's ADR 0042 origin follow
    from the reading that fired, not from the shape of the id.
    """

    node_id: str
    resolution: str
    origin: str


#: A rule reads one hint against the merged graph and either names a target or
#: declines. ``nodes`` is the whole merged node set; ``module_index`` maps a
#: dotted Python module to its ``file:`` node.
ImportAttrRule = Callable[[ImportAttrHint, dict, dict], ImportAttrTarget | None]


def resolve_submodule(
    hint: ImportAttrHint,
    nodes: dict[str, dict],
    module_index: dict[str, str],
) -> ImportAttrTarget | None:
    """``from PARENT import CHILD`` where ``PARENT.CHILD`` is a real module.

    The target is the attribute's own symbol under that submodule. It is minted
    speculatively when the module defines no such name, exactly as the strategy
    mints a cross-module call target it did not walk -- the module is proven,
    the member is the caller's claim, and ``origin=project`` follows from the
    proof rather than from a guess.
    """
    if hint.submodule not in module_index:
        return None
    return ImportAttrTarget(
        f"{SYMBOL_PREFIX}{hint.submodule}:{hint.attr}", "import", "project"
    )


def resolve_class_base(
    hint: ImportAttrHint,
    nodes: dict[str, dict],
    module_index: dict[str, str],
) -> ImportAttrTarget | None:
    """``from MODULE import CLASS`` where ``CLASS`` is a walked class.

    ``from weld.bm25 import BM25Corpus`` + ``BM25Corpus.from_nodes()`` means
    ``symbol:py:weld.bm25:BM25Corpus.from_nodes`` -- a node ``python_callgraph``
    already emitted, since a nested ``def`` carries its dotted qualname. So this
    rule *names* an existing node and never mints one, which is the opposite of
    :func:`resolve_submodule` and deliberate: a module is proven by its file
    node and its members are the caller's claim, while the walk that proves a
    class enumerates its whole member list in the same pass. A member that walk
    did not see is a member that is not there -- an inherited one included,
    which this refuses rather than resolve through an MRO it does not compute.

    Both halves have to be *walked*, not merely present. Measured on this repo
    at fix time, 78 edges carried the hint; 3 had a definite class base with a
    definite method under it, and 32 had a base node that was a module-level
    constant -- a dict, a compiled regex, a message template -- whose stub
    carries no ``kind``. Resolving on the base alone would have named those 32,
    a larger fabricated population than the deferral was introduced to remove.
    Requiring ``confidence=definite`` on both is what keeps one pass's
    speculative mint from justifying the next one's retarget, the same reason
    the submodule rule reads ``file:`` nodes rather than whatever claims a
    module name.
    """
    base_id = f"{SYMBOL_PREFIX}{hint.module}:{hint.base}"
    if not _is_walked_symbol(nodes.get(base_id), kind="class"):
        return None
    method_id = f"{base_id}.{hint.attr}"
    if not _is_walked_symbol(nodes.get(method_id)):
        return None
    return ImportAttrTarget(method_id, "import", "project")


def _is_walked_symbol(node: dict | None, kind: str | None = None) -> bool:
    """True for a symbol node a strategy walked, optionally of ``kind``.

    ``kind`` is set from the AST by the walk that defined the symbol and is
    absent from every speculatively minted stub, so the pair
    (``type=symbol``, ``confidence=definite``) plus an expected ``kind`` is the
    graph's own record of "something read this definition".
    """
    if not isinstance(node, dict) or node.get("type") != "symbol":
        return False
    props = node.get("props")
    if not isinstance(props, dict) or props.get("confidence") != "definite":
        return False
    return kind is None or props.get("kind") == kind


#: The ordered rule table -- the seam each reading plugs into. Rules are tried
#: in order and the first to name a target wins; when none does, the sentinel
#: stands, which is what the strategy already emitted. Each entry is
#: ``(name, rule)``; the name exists so a rule can be identified in a test or a
#: trace without depending on tuple position.
#:
#: Order is not load-bearing between these two: one hint cannot satisfy both,
#: because ``PARENT.CHILD`` is either a module in the index or a symbol inside
#: ``PARENT``, never both. The cheaper index lookup is simply asked first.
IMPORT_ATTR_RULES: tuple[tuple[str, ImportAttrRule], ...] = (
    ("submodule", resolve_submodule),
    ("class_base", resolve_class_base),
)


def rewrite_import_attr_targets(
    nodes: dict[str, dict], edges: list[dict], path_index: dict[str, str],
) -> None:
    """Re-derive every deferred attribute call against the merged graph.

    Restores each hinted endpoint to its sentinel first, so the outcome is a
    function of the hint plus the current graph and never of what an earlier
    round concluded -- see the module docstring for why that ordering is the
    incremental == full contract and not just tidiness.
    """
    hinted = [
        (edge, hint)
        for edge in edges
        for hint in (read_import_attr_hint(edge.get("props")),)
        if hint is not None
    ]
    if not hinted:
        return
    sentinels = _restore_sentinels(nodes, hinted)
    module_index = python_module_index(path_index)
    if not _apply_rules(nodes, hinted, module_index):
        return
    collapse_collisions(edges, _moved_by_this_pass)
    _drop_unreferenced(nodes, edges, sentinels)


def _restore_sentinels(
    nodes: dict[str, dict], hinted: list[tuple[dict, ImportAttrHint]],
) -> set[str]:
    """Put every hinted endpoint back on the sentinel and report which ones.

    Rewrites the edge's own resolution props too, not just the endpoint: they
    are what the strategy stamps beside a sentinel, and leaving a ``resolved:
    True`` beside a restored one would leave the graph in a third state that is
    neither path's.
    """
    sentinels: set[str] = set()
    for edge, hint in hinted:
        sentinel = f"{_UNRESOLVED_PREFIX}{hint.attr}"
        sentinels.add(sentinel)
        props = edge["props"]
        if edge.get(hint.side) != sentinel:
            edge[hint.side] = sentinel
            # A re-export retarget recorded on this edge can only have moved
            # an endpoint this pass had already moved, so its record is void.
            for _side, key in STUB_PROPS:
                props.pop(key, None)
        resolution = "builtin" if is_builtin_name(hint.attr) else "unresolved"
        props["resolved"] = False
        props["confidence"] = "speculative"
        props["resolution"] = resolution
        nodes.setdefault(
            sentinel,
            make_sentinel_node(
                sentinel, resolution, origin_for_sentinel(resolution)
            ),
        )
    return sentinels


def _apply_rules(
    nodes: dict[str, dict],
    hinted: list[tuple[dict, ImportAttrHint]],
    module_index: dict[str, str],
) -> bool:
    """Run the rule table over every hinted edge; report whether any moved."""
    moved = False
    for edge, hint in hinted:
        target = _first_target(hint, nodes, module_index)
        if target is None:
            continue
        edge[hint.side] = target.node_id
        props = edge["props"]
        props["resolved"] = True
        props["confidence"] = "definite"
        props["resolution"] = target.resolution
        nodes.setdefault(
            target.node_id,
            make_resolved_target_node(target.node_id, target.origin),
        )
        moved = True
    return moved


def _first_target(
    hint: ImportAttrHint,
    nodes: dict[str, dict],
    module_index: dict[str, str],
) -> ImportAttrTarget | None:
    """The first rule's answer, or ``None`` when every rule declines."""
    for _name, rule in IMPORT_ATTR_RULES:
        target = rule(hint, nodes, module_index)
        if target is not None:
            return target
    return None


def _moved_by_this_pass(edge: dict) -> bool:
    """True for an edge this pass may have retargeted -- see collapse_collisions."""
    props = edge.get("props")
    return isinstance(props, dict) and IMPORT_ATTR_PROP in props


def _drop_unreferenced(
    nodes: dict[str, dict], edges: list[dict], sentinels: set[str],
) -> None:
    """Drop each restored sentinel no edge names any more.

    The sentinel id is a bare-name namespace shared by every strategy that
    failed to resolve the same name, so this counts references rather than
    popping the way the re-export retarget can: another call site's ``work()``
    may still be unresolved. Both endpoints are counted, for the same reason
    bd oao53's purge counts inbound edges of any type -- what makes the node
    worth keeping is that something still points at it, not which direction.
    """
    referenced = {e.get(side) for e in edges for side in ("from", "to")}
    for sentinel in sentinels:
        if sentinel not in referenced:
            nodes.pop(sentinel, None)


__all__ = [
    "IMPORT_ATTR_RULES",
    "ImportAttrRule",
    "ImportAttrTarget",
    "resolve_class_base",
    "resolve_submodule",
    "rewrite_import_attr_targets",
]
