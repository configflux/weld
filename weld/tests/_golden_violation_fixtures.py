"""Producer-minted golden violations, one per class (ADR 0139 mechanism 5).

bd 5038-ipa1e's acceptance criterion is that an injected violation fails *both*
the compare and the regen path of every applicable golden family. That needs a
synthetic golden per violation class, in four families -- so the three
injections live here once rather than four times, and each family's test spends
its lines on the two paths instead of on re-deriving a broken graph.

Everything a purge or shadow rule keys on comes from the real producers --
:func:`weld.graph_closure._ensure_package_node` for the external placeholder,
:func:`weld.graph_closure._add_edge` for its anchor -- exactly as
``graph_invariants_orphan_stubs_test`` does, and for the same reason (ADR 0139
mechanism 1): the rules read ``props.source_strategy`` and ``props.authority``,
so a node literal that drifted from what the producer stamps would leave these
injections matching nothing while still looking like violations.

The *base* payload is always a real golden read off disk, deep-copied first.
Nothing here writes, and no caller may pass the copy back to a golden path that
could persist it.
"""

from __future__ import annotations

import copy
from typing import Any

from weld.graph_closure import _add_edge, _ensure_package_node

#: An endpoint no graph holds. Spelled as a ``file:`` id because that is the
#: shape ``_classify`` reaches its "no such root node" branch through -- a
#: separator-bearing id would take the federated branch and report differently.
ABSENT_NODE_ID = "file:no_such_node_in_this_graph"

#: Props matching what ``graph_closure`` stamps on the edges it appends, so an
#: injected edge is indistinguishable from a produced one to every rule that
#: reads them.
_EDGE_PROPS = {"source_strategy": "graph_closure", "confidence": "inferred"}


def _mutable(payload: Any) -> dict:
    """A deep copy, so an injection can never reach the golden on disk."""
    if not isinstance(payload, dict):
        raise AssertionError(f"expected a graph object, got {type(payload).__name__}")
    return copy.deepcopy(payload)


def _parts(payload: dict) -> tuple[dict[str, dict], list[dict]]:
    """The dict-nodes / list-edges spelling every golden in this tree uses.

    ``_graph_invariants.graph_nodes`` accepts both on-wire shapes, but an
    injection has to *write* into the payload, and the two shapes do not take
    the same write. Every shipped golden is this one; a future golden in the
    other spelling should fail loudly here rather than be injected into silently
    and produce a green "violation rejected" test that injected nothing.
    """
    nodes = payload.setdefault("nodes", {})
    edges = payload.setdefault("edges", [])
    if not isinstance(nodes, dict) or not isinstance(edges, list):
        raise AssertionError(
            "golden is not the {nodes: dict, edges: list} spelling these "
            f"injections write into: nodes={type(nodes).__name__}, "
            f"edges={type(edges).__name__}"
        )
    return nodes, edges


def _an_anchor(nodes: dict[str, dict], edges: list[dict]) -> str:
    """Some node id already in the payload, to hang an injected edge off."""
    for node_id in nodes:
        return node_id
    for edge in edges:
        source = edge.get("from")
        if isinstance(source, str) and source:
            return source
    raise AssertionError("golden holds neither a node nor an edge to anchor to")


def _first_party_module(nodes: dict[str, dict]) -> str:
    """A module name this payload demonstrably holds first-party.

    Read off a ``file:`` node's own id, because that is what
    ``_graph_invariants._module_suffixes`` builds its first-party set from: the
    trailing path segment is a suffix of every such id, so an external package
    minted under that name shadows a module the graph already resolves locally
    -- which is finding N4, reproduced rather than described.
    """
    for node_id in nodes:
        if not node_id.startswith("file:"):
            continue
        segment = node_id[len("file:"):].replace("\\", "/").rsplit("/", 1)[-1]
        if segment:
            return segment
    raise AssertionError(
        "golden holds no file: node, so nothing can be shadowed first-party; "
        "use with_emptied_placeholder for a family whose goldens are fragments"
    )


def with_dangling_edge(payload: Any) -> dict:
    """One edge whose ``to`` names a node nobody can look up."""
    injected = _mutable(payload)
    nodes, edges = _parts(injected)
    _add_edge(
        edges, _an_anchor(nodes, edges), ABSENT_NODE_ID, "depends_on",
        dict(_EDGE_PROPS),
    )
    return injected


def with_fabricated_external(payload: Any) -> dict:
    """An ``external=True`` package node shadowing a module held first-party.

    Anchored by an importer edge on purpose: an unanchored placeholder is *also*
    an emptied one, and an injection that tripped two invariants at once could
    not tell which of them the family's hook actually ran.
    """
    injected = _mutable(payload)
    nodes, edges = _parts(injected)
    module = _first_party_module(nodes)
    package_id = _ensure_package_node(nodes, module, "python")
    _add_edge(
        edges, _an_anchor(nodes, edges), package_id, "depends_on",
        dict(_EDGE_PROPS),
    )
    return injected


def with_emptied_placeholder(payload: Any) -> dict:
    """An external package placeholder with no anchor left to justify it.

    Named for a stdlib module of a language no golden here holds, so it cannot
    accidentally shadow a first-party name and trip the previous invariant
    instead of this one.
    """
    injected = _mutable(payload)
    nodes, _ = _parts(injected)
    _ensure_package_node(nodes, "encoding/json", "go")
    return injected
