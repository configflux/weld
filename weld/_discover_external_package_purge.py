"""Purge a consumer-side external package placeholder once its last
importer edge is gone (bd pkz2s; bd ukt95 and bd 0cobr widened the
strategy allowlist).

:func:`weld.graph_closure._ensure_package_node` mints a ``package`` node
for every import that does not resolve to a local file or module -- e.g.
Go's stdlib ``strings``, Python's ``os``, an npm package never vendored in
this tree. The node carries ``props.source_strategy == "graph_closure"``
and ``props.authority == "external"``, and no ``props.file`` at all: its
only reason to exist is the inbound ``depends_on`` edge(s) from whichever
files import it.

:func:`weld.discovery_state.purge_stale_nodes` matches nodes to purge by
``props.file`` alone (the mechanism bd g7rs's own docstring explains), so
this node -- carrying none -- is never a purge candidate through that path.
When the last file that imported it is deleted, its last inbound
``depends_on`` edge is correctly dropped by
:func:`weld._incremental_purge.purge_edges_by_provenance`'s
endpoint-membership floor (the edge carries no usable provenance, and its
``from`` endpoint was purged) -- but the now-zero-inbound-edge package node
itself used to linger, because nothing ever looked at *its* edge count. A
fresh full discover of the same post-delete tree never mints this node at
all (nothing imports ``strings`` any more), so incremental drifted from
full: the same orphan-survival shape bd g7rs fixed, on the opposite edge
direction.

Safe by construction even for a node that is only *momentarily* orphaned:
:mod:`weld._discover_postprocess`'s ``close_graph`` pass runs once more
over the FULL merged graph (old survivors + freshly re-run dirty sources)
after :func:`weld.discovery_state.purge_stale_nodes` returns, and
``_ensure_package_node`` is a pure, deterministic function of
``(name, language)`` gated by ``nodes.setdefault`` -- so any node purged
here that a surviving or newly-dirty file still imports is re-minted with
byte-identical id and props by that later pass. Purging here can only ever
be "too early", never wrong.

This is the mirror image of bd g7rs
(:mod:`weld._discover_membership_purge`), not a variant of it, and the two
must not be conflated:

* g7rs: a *producer*-side node (``python_package``, ``csharp_package``,
  marked ``props.roles == ["package"]``) purges on zero **outgoing**
  ``contains`` edges -- its members disappeared.
* pkz2s (here): a *consumer*-side node (marked
  ``props.authority == "external"`` and ``props.source_strategy`` one of
  :data:`_EDGE_ANCHORED_STRATEGIES`) purges on zero **incoming**
  ``depends_on`` edges -- its importers/declarers disappeared.

bd ukt95 widened this rule's strategy allowlist beyond ``graph_closure``
after empirically disproving the assumption this docstring used to make
here: that ``cpp_conan``/``cpp_vcpkg`` dependency-leaf nodes are
"manifest-anchored" and therefore immune. They are not. Reading
:func:`weld.strategies.cpp_conan._emit_dep` /
:func:`weld.strategies.cpp_vcpkg.extract` shows the manifest
(``conanfile.txt``/``vcpkg.json``) only anchors the *project* node
(``props.file`` set, ``authority: "canonical"``) -- the dependency leaf
itself (``package://conan/<name>/<version>``, ``package://vcpkg/<name>``)
carries no ``props.file`` and exists solely because of the project's
inbound ``depends_on`` edge, exactly like a graph_closure placeholder. Empirical
repro (bd ukt95): deleting a project's ``conanfile.txt``/``vcpkg.json``
purges the project node via the ordinary ``props.file`` rule, drops the
edge via the endpoint-membership floor, and a fresh full discover of the
same post-delete tree mints neither node -- but pre-fix, incremental left
the now-zero-inbound dependency leaf behind. Folding ``cpp_conan``/
``cpp_vcpkg`` into this same zero-inbound-``depends_on`` rule closes that
gap exactly the way it already closes it for ``graph_closure``.

bd 0cobr empirically re-verified (real ``_discover_single_repo`` orchestrator
run, incremental vs full, over a temp git repo with an actual
``CMakeLists.txt``) that ``_cmake_packages`` (``source_strategy:
"cpp_cmake"``) shares the identical shape: ``ensure_package_sentinel``
mints the dependency leaf with no ``props.file``, ``authority: "external"``,
anchored solely by the project node's inbound ``depends_on`` edge (the
project node itself, minted by ``cpp_cmake._ensure_project_node``, carries
``props.file`` pointing at the declaring ``CMakeLists.txt`` and is purged
correctly by the ordinary rule). Deleting the ``CMakeLists.txt`` purged the
project node and dropped the edge, but pre-fix left the now-zero-inbound
leaf behind; a fresh full discover of the same post-delete tree minted
neither node. Folded ``cpp_cmake`` into this rule closes that gap the same
way bd ukt95 closed it for ``cpp_conan``/``cpp_vcpkg``.

One more ``type: "package"`` external-dependency leaf was investigated
under bd ukt95 and initially NOT folded in here:

* The C# tree-sitter using-import node
  (:func:`weld.strategies._csharp_tree_sitter._add_import_dependencies`,
  ``source_strategy: "tree_sitter"``, ``authority: "derived"`` -- NOT
  ``"external"``): bd ukt95 verified empirically (real tree-sitter parse,
  not mocked) that this node is *not* manifest-anchored at all -- deleting
  its ``.csproj``/``PackageReference`` degrades ``props.origin`` from
  ``"external"`` to ``"unresolved"`` but the node survives via its true
  anchor, a surviving ``.cs`` ``using`` statement. So the "manifest
  deleted" scenario this file's rule exists to catch is a non-issue for
  it. ``authority: "derived"`` keeps THIS rule from reaching it (this
  rule requires ``authority == "external"``), so widening
  ``_EDGE_ANCHORED_STRATEGIES`` to include ``"tree_sitter"`` would be dead
  code -- the authority check alone would still exclude every instance.
  bd 5ouuf gave it (and its identically-shaped Java sibling,
  :func:`weld.strategies._java_tree_sitter._add_import_dependencies`) a
  SEPARATE disjoint rule instead --
  :func:`weld._discover_tree_sitter_package_purge.emptied_tree_sitter_package_node_ids`,
  keyed on ``authority == "derived"`` plus ``props.origin`` -- for the
  different scenario this file's own rule does not cover (last importing
  file deleted, manifest untouched). bd 5ouuf initially scoped that rule to
  ``props.origin in {"external", "unresolved"}``, leaving a
  ``"project"``/``"stdlib"`` gap it had itself empirically confirmed; bd
  cs0rt closed that gap after showing the asymmetry-of-harm concern behind
  the exclusion does not hold -- see that module's docstring for the full
  argument (source_strategy already makes a producer-anchored id
  categorically unreachable here, and the collision that would matter is
  provably resolved in the producer's favor by the discovery merge order).

:func:`emptied_placeholder_node_ids` is the single entry point
:mod:`weld.discovery_state` calls -- it unions this rule with g7rs's,
(bd oao53 amendment) a third rule from
:mod:`weld._discover_unresolved_symbol_purge` for call/inherits/implements
``symbol:unresolved:<name>`` sentinels, a placeholder shape that mirrors
this file's own consumer-side reasoning (no ``props.file``, purge once no
edge names it) but cannot reuse this file's ``depends_on``-only signal --
see that module's docstring for why -- (bd n4nvt amendment) a
fourth rule from :mod:`weld._discover_resolved_stub_purge` for
``weld.strategies._python_origin.make_resolved_target_node``'s resolved
cross-glob call-target stubs: the same no-``props.file`` shape again, but at
a ``symbol:py:<module>:<qual>`` id indistinguishable in FORM from a real
symbol id, so it keys on the node's own props rather than id shape -- see
that module's docstring for why -- and (bd 5ouuf amendment) a fifth rule
from :mod:`weld._discover_tree_sitter_package_purge` for the C#/Java
tree-sitter using/import shell described above, disjoint from this file's
own rule on ``props.authority`` (``"derived"`` vs ``"external"``). All five
key off disjoint node shapes, so the caller's post-purge fixed-point
(remove, widen ``removed_ids``, re-run the provenance edge purge once more)
treats every placeholder shape identically without needing to know there
are five.
"""

from __future__ import annotations

from weld._discover_membership_purge import emptied_membership_node_ids
from weld._discover_resolved_stub_purge import emptied_resolved_stub_node_ids
from weld._discover_tree_sitter_package_purge import (
    emptied_tree_sitter_package_node_ids,
)
from weld._discover_unresolved_symbol_purge import emptied_unresolved_symbol_node_ids

_PACKAGE_TYPE = "package"
_DEPENDS_ON_EDGE_TYPE = "depends_on"
_EXTERNAL_AUTHORITY = "external"

#: ``source_strategy`` values whose ``type: "package"`` + ``authority:
#: "external"`` output is anchored ONLY by inbound ``depends_on`` edges --
#: never by ``props.file``, never by anything a membership rule could see
#: -- so "zero inbound depends_on" is the complete purge condition for
#: every node any of them mints. ``graph_closure`` is the original member
#: (bd pkz2s); ``cpp_conan``/``cpp_vcpkg`` joined after bd ukt95 proved
#: their dependency-leaf nodes share the identical shape despite being
#: minted from a manifest file rather than purely from an import;
#: ``cpp_cmake`` joined after bd 0cobr empirically re-verified the same
#: shape end-to-end (see the module docstring above for both). The one
#: shape investigated and deliberately left out -- the C# tree-sitter
#: using-import node -- is documented there too.
_EDGE_ANCHORED_STRATEGIES = frozenset(
    {"graph_closure", "cpp_conan", "cpp_vcpkg", "cpp_cmake"},
)


def _is_edge_anchored_external_package(node: dict) -> bool:
    """Return True iff *node* is output from an :data:`_EDGE_ANCHORED_STRATEGIES` member.

    Defensive the same way ``_discover_membership_purge._is_membership_anchored``
    reads ``props``: strategy-authored props (including project-local
    overrides) are untrusted shape, so a missing/non-dict ``props`` reads as
    "not an edge-anchored external placeholder" rather than raising.

    The ``isinstance`` on the *value* is the other half of that posture, and
    is load-bearing rather than ceremony (bd 5038-53jjg): these props are read
    back off ``.weld/graph.json``, which ADR 0115 treats as unvetted repo text,
    and an unhashable value there (``"source_strategy": []``) made the
    membership test raise rather than answer -- taking this purge, and the
    whole incremental discover around it, down with it. Non-string reads as
    "not an edge-anchored external placeholder", the same safe side a missing
    ``props`` lands on: retain a node rather than purge one. Same guard, same
    reason, as :func:`weld._discover_placeholder_anchor._is_derived_edge`
    (bd 5038-rwi34).
    """
    if node.get("type") != _PACKAGE_TYPE:
        return False
    props = node.get("props") or {}
    if not isinstance(props, dict):
        return False
    strategy = props.get("source_strategy")
    return (
        isinstance(strategy, str)
        and strategy in _EDGE_ANCHORED_STRATEGIES
        and props.get("authority") == _EXTERNAL_AUTHORITY
    )


def emptied_external_package_node_ids(
    nodes: dict[str, dict], edges: list[dict],
) -> set[str]:
    """Return ids of edge-anchored external package nodes with zero inbound edges.

    Call after the ordinary ``props.file`` purge and its edge purge, over
    their *result*: a node found here already lost every inbound
    ``depends_on`` edge from its importer/declarer(s) -- each dropped by the
    endpoint-membership floor when the importing/declaring file's own node
    was purged, or by the provenance rule if the edge carried one -- so
    nothing here re-derives *why*, it only names the node whose sole reason
    to exist just went with it. A full discover never mints such a node
    unless something currently imports or declares it, so purging it here
    keeps incremental discovery's output equal to a fresh full run's for
    every :data:`_EDGE_ANCHORED_STRATEGIES` member (bd pkz2s's
    ``graph_closure`` shape and bd ukt95/bd 0cobr's
    ``cpp_conan``/``cpp_vcpkg``/``cpp_cmake`` manifest-dependency-leaf
    shape alike).

    A node with a surviving importer/declarer (>=1 inbound ``depends_on``
    edge) is never returned, regardless of how many other importers it
    lost -- matching what a full run over the same partially-emptied tree
    would still emit.
    """
    have_inbound_depends_on: set[str] = set()
    for edge in edges:
        if edge.get("type") != _DEPENDS_ON_EDGE_TYPE:
            continue
        to_id = edge.get("to")
        if isinstance(to_id, str):
            have_inbound_depends_on.add(to_id)

    return {
        nid
        for nid, node in nodes.items()
        if _is_edge_anchored_external_package(node)
        and nid not in have_inbound_depends_on
    }


def emptied_placeholder_node_ids(
    nodes: dict[str, dict], edges: list[dict],
) -> set[str]:
    """Union of five independently-scoped placeholder-node rules.

    Single entry point for :func:`weld.discovery_state.purge_stale_nodes`:
    g7rs's producer-side rule (``roles`` contains ``"package"``, purges on
    zero outgoing ``contains``), pkz2s's consumer-side rule (widened by bd
    ukt95 and bd 0cobr -- ``source_strategy`` in
    :data:`_EDGE_ANCHORED_STRATEGIES` plus ``authority == "external"``,
    purges on zero incoming ``depends_on``), oao53's
    unresolved-symbol-sentinel rule (id prefix ``symbol:unresolved:``,
    purges once no surviving edge names it, in EITHER direction and of ANY
    type -- see :mod:`weld._discover_unresolved_symbol_purge` for why that
    one cannot be scoped to a single edge type the way the first two are,
    and :mod:`weld._discover_placeholder_anchor` for why not to a single
    direction either, bd 5038-q4t3d), n4nvt's
    resolved-cross-glob-stub rule (props-keyed, NOT id-prefix-keyed, since
    this id shape collides with real symbol ids -- see
    :mod:`weld._discover_resolved_stub_purge` -- also purged once no edge of
    any type names it either way, for the same shared-namespace reason
    oao53's rule needs it),
    and bd 5ouuf's tree-sitter using/import-shell rule (``source_strategy ==
    "tree_sitter"`` plus ``authority == "derived"`` plus ``props.origin`` in
    ``{"external", "unresolved"}`` -- see
    :mod:`weld._discover_tree_sitter_package_purge` -- purges on zero
    incoming ``depends_on``, disjoint from pkz2s's rule on the authority
    value alone). The five key off disjoint node shapes, so a given node can
    only ever match one of them -- this is a plain union, never a merge of
    the underlying logic.
    """
    return (
        emptied_membership_node_ids(nodes, edges)
        | emptied_external_package_node_ids(nodes, edges)
        | emptied_unresolved_symbol_node_ids(nodes, edges)
        | emptied_resolved_stub_node_ids(nodes, edges)
        | emptied_tree_sitter_package_node_ids(nodes, edges)
    )


__all__ = [
    "emptied_external_package_node_ids",
    "emptied_placeholder_node_ids",
]
