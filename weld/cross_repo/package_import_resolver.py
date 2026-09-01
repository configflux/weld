"""Cross-repo resolver: detect package imports across repo boundaries.

This resolver scans each child graph for *package consumer* nodes -- nodes
that carry an ``imports_from`` list -- and matches those import names
against *package producer* nodes (``type="package"``) declared in sibling
children. When a match is found, the resolver emits a
``cross_repo:depends_on`` edge from the importing consumer to the target
package, namespaced with the child name and the ASCII Unit Separator per
the federation ID convention. The type is the shared
``CROSS_REPO_DEPENDS_ON`` from :mod:`weld._federation_endpoints`; this
resolver spelled its own un-namespaced ``depends_on`` until bd
``5038-4v6fm``.

The resolver is language-neutral. A consumer is any node whose ``type``
is in :data:`_CONSUMER_TYPES` and whose props carry a non-empty
``imports_from`` list. Both ``python_module`` (writes ``type="file"`` --
see weld/strategies/python_module.py) and the shared tree-sitter C#
strategy (weld/strategies/_csharp_tree_sitter.py, ``imports_from``
populated from ``using`` directives) produce this shape, so no
per-language branching is required. The legacy ``"python_module"`` type
is retained in the allowlist so historical fixtures still resolve.

The resolver skips imports that resolve within the same child (no
self-edges) and silently ignores imports that match no sibling package.
Output ordering is deterministic: edges are sorted by a composite key of
(source child, source node, target child, target node, import name) so
that repeated runs against identical input produce byte-identical output.
"""

from __future__ import annotations

from weld._federation_endpoints import CROSS_REPO_DEPENDS_ON
from weld.cross_repo.base import (
    CrossRepoEdge,
    CrossRepoResolver,
    ResolverContext,
    _iter_nodes,
    register_resolver,
)
from weld.workspace import UNIT_SEPARATOR

#: Node ``type`` values that legitimately carry an ``imports_from`` list.
#: ``"file"`` is the real production form for both Python (set at
#: weld/strategies/python_module.py:238) and C# (set by the shared
#: tree-sitter strategy in weld/strategies/tree_sitter.py:351 with
#: ``imports_from`` populated by
#: weld/strategies/_csharp_tree_sitter.py:83). ``"python_module"`` is
#: retained for legacy/test fixtures that predate the rename to
#: ``"file"``. Adding a new language is a no-op here unless that
#: language emits a fundamentally different node ``type``: extend this
#: tuple rather than branching per language.
_CONSUMER_TYPES: frozenset[str] = frozenset({"file", "python_module"})


def _build_package_index(
    context: ResolverContext,
) -> dict[str, list[tuple[str, str]]]:
    """Build a mapping from package name to (child_name, node_id) pairs.

    Scans every child graph for nodes whose ``type`` is ``"package"`` and
    whose ``props.name`` field is a non-empty string. The result maps
    each package name to the list of (child, node-id) pairs that
    declare it. Multiple children may declare the same package name;
    the resolver emits an edge to each one.

    The node values follow the production :class:`weld.graph.Graph`
    contract (``{type, label, props}``); the ``name`` lookup goes
    through ``props``, mirroring the access pattern in
    :mod:`weld.cross_repo.grpc_service_binding`. Returns a plain dict so
    iteration order is insertion-stable. Entries are sorted by
    (child_name, node_id) for determinism.
    """
    index: dict[str, list[tuple[str, str]]] = {}
    for child_name in sorted(context.children):
        graph = context.children[child_name]
        for node_id, node in _iter_nodes(graph):
            if not isinstance(node, dict):
                continue
            if node.get("type") != "package":
                continue
            props = node.get("props") or {}
            pkg_name = props.get("name")
            if not pkg_name or not isinstance(pkg_name, str):
                continue
            if not node_id:
                continue
            index.setdefault(pkg_name, []).append((child_name, str(node_id)))
    return index


@register_resolver("package_import_resolver")
class PackageImportResolver(CrossRepoResolver):
    """Match ``imports_from`` entries against sibling package declarations.

    For each *package consumer* node in each child -- a node whose
    ``type`` is in :data:`_CONSUMER_TYPES` and that carries a non-empty
    ``imports_from`` list -- the resolver iterates over the imports and
    looks each entry up in the package index built from all children.
    Matches against the same child are skipped (intra-repo imports are
    not cross-repo edges). Each cross-child match produces a
    ``depends_on`` edge with props ``import_name`` and ``source_child``.

    The consumer detection is language-neutral: any tree-sitter or
    AST-backed strategy that emits a file node with ``imports_from``
    (Python ``python_module``, the shared tree-sitter strategy for C#
    ``using`` directives, and future languages that follow the same
    file-anchor convention) participates without a per-language branch.
    """

    name = "package_import_resolver"

    def resolve(self, context: ResolverContext) -> list[CrossRepoEdge]:
        """Return cross-repo ``depends_on`` edges for matched package imports."""
        pkg_index = _build_package_index(context)
        if not pkg_index:
            return []

        edges: list[CrossRepoEdge] = []

        for child_name in sorted(context.children):
            graph = context.children[child_name]
            for node_id, node in _iter_nodes(graph):
                if not isinstance(node, dict):
                    continue
                # Language-neutral consumer detection: accept file/module
                # node shapes that legitimately carry an ``imports_from``
                # list. ``function``/``symbol``/etc nodes are skipped
                # even if they happen to carry that field -- the
                # ``imports_from`` contract is anchored at the file
                # level by every strategy in the family.
                if node.get("type") not in _CONSUMER_TYPES:
                    continue
                # The production :class:`weld.graph.Graph` shape stores
                # ``imports_from`` under ``props`` (set by
                # weld/strategies/python_module.py:238 and
                # weld/strategies/_csharp_tree_sitter.py:83). The
                # resolver previously read it at the top level, which
                # silently produced zero edges against the real Graph.
                props = node.get("props") or {}
                imports_from = props.get("imports_from")
                if not imports_from or not isinstance(imports_from, list):
                    continue
                if not node_id:
                    continue

                for imp_name in sorted(imports_from):
                    if not isinstance(imp_name, str) or not imp_name:
                        continue
                    targets = pkg_index.get(imp_name)
                    if not targets:
                        continue
                    for target_child, target_node_id in targets:
                        # Skip intra-repo matches (no self-edges).
                        if target_child == child_name:
                            continue
                        edges.append(
                            CrossRepoEdge(
                                from_id=f"{child_name}{UNIT_SEPARATOR}{node_id}",
                                to_id=f"{target_child}{UNIT_SEPARATOR}{target_node_id}",
                                type=CROSS_REPO_DEPENDS_ON,
                                props={
                                    # ADR 0050: matching is by name
                                    # alone -- the importing module
                                    # writes ``imports_from: ["foo"]``
                                    # and a sibling repo declares a
                                    # ``package`` whose ``name`` is
                                    # ``"foo"``. There is no method
                                    # signature, no version, no
                                    # path-based disambiguation.
                                    # Ambiguous package names will
                                    # over-include, so the edge is
                                    # `speculative` and lands in the
                                    # ADR 0055 review queue.
                                    "source_strategy": "package_import_resolver",
                                    "confidence": "speculative",
                                    "import_name": imp_name,
                                    "source_child": child_name,
                                },
                            )
                        )

        return edges
