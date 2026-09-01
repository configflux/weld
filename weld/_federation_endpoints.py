"""How a cross-repo edge endpoint is spelled, in one place (ADR 0137 ss1-2).

A federated root graph holds **two** id spaces, and an endpoint belongs to
exactly one of them:

* a node that lives inside a child graph is namespaced --
  ``<child>\\x1f<child-local-id>`` -- and is resolved in that child at read
  time (ADR 0011 ss7, ADR 0089);
* a whole repository is the root-minted ``repo:<name>``, resolved in the root
  meta-graph directly. It carries no namespace, because there is nothing to
  resolve inside a child.

The rule is one line: **prefix with a child namespace if and only if the id
came out of that child's own graph.** ``<child>\\x1frepo:<child>`` obeys
neither space -- it asks a reader to look a root-minted id up inside a child --
and every edge that used it dangled, in the root graph, past a validator, and
into a ``wd impact`` answer that then blamed the configuration.

This module is that vocabulary and nothing else, because the defect was
duplication: two inline f-strings minted endpoints and three ad-hoc separator
splits read them, so the wrong spelling could be copied from one resolver to
another with nothing to copy *from*. Everything that builds or parses an
endpoint imports from here.

It lives beside :data:`weld.workspace.UNIT_SEPARATOR` rather than in
:mod:`weld.federation_support` (which re-exports it, and is the surface ADR
0137 ss2 names) for a build reason: ``//weld/cross_repo`` is a deliberate leaf
target that must not depend on ``//weld:runtime``, and the cross-repo resolvers
are exactly the callers that have to build these ids.
"""

from __future__ import annotations

from weld.workspace import UNIT_SEPARATOR

__all__ = [
    "CROSS_REPO_DEPENDS_ON",
    "REPO_NODE_PREFIX",
    "edge_child_names",
    "endpoint_child_name",
    "prefix_node_id",
    "repo_node_id",
    "split_prefixed_id",
]

#: Prefix of a node id the *root* meta-graph mints for a child repository
#: (:mod:`weld.federation_root`). Root-minted ids exist in no child graph.
REPO_NODE_PREFIX: str = "repo:"

#: Edge type for the cross-repo "A depends on B" relationship, in one place
#: (bd ``5038-4v6fm``, recorded as a known follow-up in ADR 0137).
#:
#: The endpoint half of this module's docstring applies unchanged to the type:
#: three resolvers produce this one fact by three routes -- ``package_graph``
#: from build manifests, ``compose_topology`` from ``depends_on`` in a compose
#: file, ``package_import_resolver`` from import evidence -- and each spelled
#: its own literal, so two of them drifted to the un-namespaced ``depends_on``
#: with nothing to copy *from*. Plain ``depends_on`` is a legitimate intra-repo
#: type in ``VALID_EDGE_TYPES``, so the drifted edges validated as ordinary
#: edges and the split stayed invisible.
#:
#: The ``cross_repo:`` prefix is what
#: :func:`weld._federation_validate.is_well_formed_cross_repo_edge_type` admits
#: on a federated root graph. That checker is deliberately *not* imported here
#: -- it belongs to the dependency-free contract library, and importing it
#: would put ``//weld:contract`` under every resolver. The two are held
#: together by test instead, which is the point of the mechanism:
#: ``weld/tests/weld_cross_repo_edge_type_parity_test.py`` runs every
#: registered resolver and holds its output to the checker.
CROSS_REPO_DEPENDS_ON: str = "cross_repo:depends_on"


def prefix_node_id(child_name: str, node_id: str) -> str:
    """Return the canonical federated ID for a *child-local* node ID.

    Only for ids that came out of *child_name*'s own graph. A root-minted id
    goes through :func:`repo_node_id` instead.
    """
    return f"{child_name}{UNIT_SEPARATOR}{node_id}"


def split_prefixed_id(node_id: str) -> tuple[str, str] | None:
    """Split a canonical federated ID into ``(child_name, original_id)``."""
    if UNIT_SEPARATOR not in node_id:
        return None
    return node_id.split(UNIT_SEPARATOR, 1)


def repo_node_id(child_name: str) -> str:
    """Return the root-minted ``repo:<child_name>`` node id.

    The one place this string is built. :mod:`weld.federation_root` mints the
    node with it and every resolver that joins two repositories points at it
    with the same call, so the two can no longer drift apart.
    """
    return f"{REPO_NODE_PREFIX}{child_name}"


def endpoint_child_name(node_id: str) -> str | None:
    """Return the child an edge endpoint names, or ``None`` for neither shape.

    Understands both spellings: the namespace prefix of ``<child>\\x1f<local>``
    and the name carried by ``repo:<name>``. ``None`` for any other root node
    id, and for a malformed federated id whose child half is empty.

    Ask here rather than splitting on the separator: a raw split answers
    ``None`` for every repo-level endpoint, which is how an edge *between two
    repositories* came to look like an edge that touched no repository at all.
    """
    parts = split_prefixed_id(node_id)
    if parts is not None:
        return parts[0] or None
    if node_id.startswith(REPO_NODE_PREFIX):
        return node_id[len(REPO_NODE_PREFIX):] or None
    return None


def edge_child_names(from_id: str, to_id: str) -> set[str]:
    """Return every child name an edge's two endpoints name.

    Empty when neither endpoint names a child; a single entry when both name
    the same one.
    """
    return {
        name
        for name in (endpoint_child_name(from_id), endpoint_child_name(to_id))
        if name is not None
    }
