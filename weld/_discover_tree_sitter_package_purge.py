"""Purge a tree-sitter using/import package placeholder once its last
importer edge is gone (bd 5ouuf; widened to all four ADR 0042 origins by bd
cs0rt).

:func:`weld.strategies._csharp_tree_sitter._add_import_dependencies` and its
Java sibling :func:`weld.strategies._java_tree_sitter._add_import_dependencies`
each mint a ``package`` node for every ``using``/``import`` target a file
declares, regardless of whether that target resolves to a project-local
namespace, the platform standard library, a manifest-declared dependency, or
nothing at all. The node carries ``props.source_strategy == "tree_sitter"``
(the shared strategy name both language front-ends run under, not a
C#/Java-specific string), ``props.authority == "derived"``, and
``props.origin`` set to whichever of the four ADR 0042 buckets
(``"project"``, ``"stdlib"``, ``"external"``, ``"unresolved"``)
:func:`weld.strategies._csharp_origin.classify_using_import` /
:func:`weld.strategies._java_origin.classify_import_package` computed at mint
time. Like :mod:`weld._discover_external_package_purge`'s own
``graph_closure``/``cpp_conan``/``cpp_vcpkg``/``cpp_cmake`` placeholders, this
node carries no ``props.file`` at all -- its only anchor is the inbound
``depends_on`` edge(s) from whichever file(s) still ``using``/``import`` it --
so :func:`weld.discovery_state.purge_stale_nodes`'s ``props.file`` match never
sees it, and it used to linger as a zero-inbound-edge orphan once its last
importer was deleted.

Investigated under bd ukt95 and initially found NOT to share
``_discover_external_package_purge``'s ``_EDGE_ANCHORED_STRATEGIES`` shape,
because that allowlist's predicate additionally requires ``props.authority ==
"external"`` -- this node's authority is always ``"derived"``, never
``"external"``, so folding ``"tree_sitter"`` into that allowlist would be dead
code (the authority check alone would still exclude every instance). bd 5ouuf
re-investigated with a real end-to-end repro (actual ``tree_sitter_c_sharp`` /
``tree_sitter_java`` grammars, not mocked) and confirmed the SAME
zero-inbound-``depends_on`` orphan-survival shape applies here too, just under
a different authority/origin combination than the existing rule's -- hence a
new, disjoint rule rather than a widened allowlist.

bd 5ouuf initially scoped this rule to ``props.origin in {"external",
"unresolved"}`` only, deliberately excluding ``"project"``/``"stdlib"``, even
though its own investigation *empirically confirmed* the identical leak for
project-origin: a ``using``/``import`` of a project-rooted namespace that no
file's own declaration exactly matches (e.g. ``using MyApp.Deep.Nested;``
where every file under ``MyApp.Deep`` declares only ``namespace MyApp.Deep``
or shallower) mints a bare tree-sitter shell (``origin: "project"``, no
``roles`` key). The stated reason was asymmetry of harm: a project/stdlib
false negative (an extra placeholder lingering) seemed far cheaper than a
false positive (purging a live reference into the project's own code).

bd cs0rt re-examined that asymmetry argument and found it does not survive
scrutiny -- the premise that a project/stdlib false positive is *possible* at
all is false, so there is no harm to be asymmetric against:

* :func:`weld.strategies.csharp_package.extract` is the only strategy that
  ever anchors a namespace id with a *different* shape
  (``source_strategy: "csharp_package"``, ``roles: ["package"]``, no
  ``props.file``, anchored by outgoing ``contains`` instead of inbound
  ``depends_on``). This rule's own predicate already requires
  ``props.source_strategy == "tree_sitter"``, so any id ``csharp_package``
  has ever claimed is categorically outside this rule's reach -- true
  regardless of the origin allowlist below, and true regardless of the
  *inbound edge count*, so a producer node with a live inbound
  ``depends_on`` edge (which does not normally happen, but is not
  structurally impossible if the id namespace is also imported elsewhere)
  is equally protected.
* The only way that protection could fail is a same-pass race: a tree-sitter
  shell and a ``csharp_package`` claim landing on the same id in the same
  discovery pass, with the shell's claim winning. It cannot. Both
  :func:`weld.init`'s generated source list and
  :func:`weld._init_csharp.csharp_source_entries` always order the C#
  tree-sitter entry *before* ``csharp_package``, and both glob the identical
  ``"**/*.cs"``, so whenever one runs (some ``.cs`` file is dirty) the other
  runs in the very same pass. :func:`weld._discover_node_merge.claim_supersedes`
  / :func:`weld._discover_node_merge.incremental_claim_wins` -- the same
  tie-break both the full-discover branch (:mod:`weld.discover`) and the
  incremental branch (:mod:`weld._discover_incremental_merge`) apply -- award
  a same-``confidence`` tie to whichever claim is processed *later*, and both
  shapes carry ``confidence: "definite"``. ``csharp_package`` therefore always
  wins a same-pass collision, in full or incremental mode alike, by
  construction, not by chance. ``incremental_claim_wins`` also admits any
  claim on an *absent* id unconditionally, so even a same-pass ordering where
  this rule purges a shell before ``csharp_package`` reclaims that same id
  loses no information -- the producer's claim is still admitted.
* Empirically confirmed (ad-hoc, real ``tree_sitter_c_sharp`` grammar, not
  checked in -- see the bd cs0rt spec-lock comment for the exact fixtures and
  captured graphs): a project declaring ``<RootNamespace>MyApp</RootNamespace>``
  with one file declaring ``namespace MyApp.Deep.Nested`` and a second file
  ``using MyApp.Deep.Nested;`` resolves, in BOTH a from-scratch full discover
  and an incremental pass that newly introduces the declaring file, to the
  ``csharp_package`` producer shape at that id -- never the tree-sitter shell
  -- confirming the collision is resolved correctly in both discovery modes,
  not just asserted from reading the merge code.
* Java ships no producer-side package strategy at all (no
  ``java_package.py``, no discover.yaml entry for one), so every
  ``package:java:*`` node in any graph is minted solely by this shared
  tree-sitter shell -- Java carries zero collision risk, a strictly *safer*
  case than C#'s (already-safe) one.

With the collision risk eliminated, the remaining question is exactly the
one :mod:`weld._discover_external_package_purge` already answers for
``"external"``/``"unresolved"``: does a full discover ever mint a node at
this id with zero inbound ``depends_on`` edges? No -- for a pure tree-sitter
shell (by definition, an id no producer has ever claimed), its only anchor to
exist at all, in any origin bucket, is the inbound edge from whatever
``using``/``import`` still names it. Zero inbound edges is not a transient
state a full discover could ever observe; it is the exact condition under
which a full discover mints nothing. Purging here is convergence, not risk,
uniformly across all four ADR 0042 buckets.

Safe by construction the same way pkz2s's/ukt95's/0cobr's rules are: no other
strategy ever mints a node or points a non-``depends_on`` edge at a
tree-sitter-derived package id while it still carries this fingerprint
(grep-verified across ``_csharp_inheritance.py``, ``_csharp_partial_classes.py``,
and ``_java_inherits.py`` -- none reference ``package:<lang>:*`` ids at all),
and any surviving or newly-dirty file that still ``using``/``import``s the
same target re-parses through the ordinary dirty-source tree-sitter loop and
re-mints the identical id (``nodes.setdefault``, deterministic on
``(language, import name)``) plus a fresh ``depends_on`` edge before this
purge's result is ever read downstream -- so a purge here can only ever be
"too early", never wrong.

Folded into
:func:`weld._discover_external_package_purge.emptied_placeholder_node_ids` as
a fifth, disjoint rule -- disjoint because this one requires
``props.authority == "derived"`` where the existing edge-anchored-external
rule requires ``props.authority == "external"``, so a given node can only
ever match one of the two package-shaped rules.
"""

from __future__ import annotations

_PACKAGE_TYPE = "package"
_DEPENDS_ON_EDGE_TYPE = "depends_on"
_TREE_SITTER_STRATEGY = "tree_sitter"
_DERIVED_AUTHORITY = "derived"

#: All four ADR 0042 origin buckets are safe to purge at zero inbound
#: ``depends_on`` edges -- see the module docstring for why the
#: ``"project"``/``"stdlib"`` exclusion bd 5ouuf started with does not hold
#: up (bd cs0rt). Kept as an explicit allowlist rather than removed outright
#: so a missing/garbage ``origin`` value (untrusted strategy-authored props,
#: same posture as every other check in this module) still reads as
#: "not eligible" rather than "purge by default".
_PURGEABLE_ORIGINS = frozenset({"external", "unresolved", "project", "stdlib"})


def _is_tree_sitter_package_shell(node: dict) -> bool:
    """Return True iff *node* is an ``_add_import_dependencies`` shell
    eligible for this rule.

    Defensive the same way the sibling rules read ``props``: strategy-authored
    props (including project-local overrides) are untrusted shape, so a
    missing/non-dict ``props`` reads as "not eligible" rather than raising.
    """
    if node.get("type") != _PACKAGE_TYPE:
        return False
    props = node.get("props") or {}
    if not isinstance(props, dict):
        return False
    return (
        props.get("source_strategy") == _TREE_SITTER_STRATEGY
        and props.get("authority") == _DERIVED_AUTHORITY
        and props.get("origin") in _PURGEABLE_ORIGINS
    )


def emptied_tree_sitter_package_node_ids(
    nodes: dict[str, dict], edges: list[dict],
) -> set[str]:
    """Return ids of tree-sitter using/import package shells with zero
    inbound ``depends_on`` edges, across all four ADR 0042 origins.

    Call after the ordinary ``props.file`` purge and its edge purge, over
    their *result*: a node found here already lost every inbound
    ``depends_on`` edge from its importer(s) -- each dropped by the
    endpoint-membership floor when the importing file's own node was purged,
    or by the provenance rule if the edge carried one -- so nothing here
    re-derives *why*, it only names the node whose sole reason to exist just
    went with it. A full discover never mints such a node unless something
    currently imports it, so purging it here keeps incremental discovery's
    output equal to a fresh full run's for every origin this shape can carry
    (see the module docstring for why project/stdlib join external/unresolved
    here instead of staying excluded).

    A node with a surviving importer (>=1 inbound ``depends_on`` edge) is
    never returned, regardless of how many other importers it lost --
    matching what a full run over the same partially-emptied tree would
    still emit.
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
        if _is_tree_sitter_package_shell(node)
        and nid not in have_inbound_depends_on
    }


__all__ = ["emptied_tree_sitter_package_node_ids"]
