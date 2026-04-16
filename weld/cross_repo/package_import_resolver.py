"""Cross-repo resolver: detect Python package imports across repo boundaries.

This resolver scans each child graph for ``python_module`` nodes that carry
an ``imports_from`` list and matches those import names against ``package``
nodes declared in sibling children. When a match is found, the resolver
emits a ``depends_on`` edge from the importing module to the target package,
namespaced with the child name and the ASCII Unit Separator per the
federation ID convention.

The resolver skips imports that resolve within the same child (no
self-edges) and silently ignores imports that match no sibling package.
Output ordering is deterministic: edges are sorted by a composite key of
(source child, source node, target child, target node, import name) so
that repeated runs against identical input produce byte-identical output.
"""

from __future__ import annotations

from weld.cross_repo.base import (
    CrossRepoEdge,
    CrossRepoResolver,
    ResolverContext,
    register_resolver,
)
from weld.workspace import UNIT_SEPARATOR


def _build_package_index(
    context: ResolverContext,
) -> dict[str, list[tuple[str, str]]]:
    """Build a mapping from package name to (child_name, node_id) pairs.

    Scans every child graph for nodes whose ``type`` is ``"package"`` and
    whose ``name`` field is a non-empty string. The result maps each
    package name to the list of (child, node-id) pairs that declare it.
    Multiple children may declare the same package name; the resolver
    emits an edge to each one.

    Returns a plain dict so iteration order is insertion-stable. Entries
    are sorted by (child_name, node_id) for determinism.
    """
    index: dict[str, list[tuple[str, str]]] = {}
    for child_name in sorted(context.children):
        graph = context.children[child_name]
        nodes = getattr(graph, "nodes", None)
        if nodes is None:
            continue
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if node.get("type") != "package":
                continue
            pkg_name = node.get("name")
            if not pkg_name or not isinstance(pkg_name, str):
                continue
            node_id = node.get("id", "")
            if not node_id:
                continue
            index.setdefault(pkg_name, []).append((child_name, str(node_id)))
    return index


@register_resolver("package_import_resolver")
class PackageImportResolver(CrossRepoResolver):
    """Match ``imports_from`` entries against sibling package declarations.

    For each ``python_module`` node in each child, the resolver iterates
    over its ``imports_from`` list and looks up each entry in the package
    index built from all children. Matches against the same child are
    skipped (intra-repo imports are not cross-repo edges). Each match
    produces a ``depends_on`` edge with props ``import_name`` and
    ``source_child``.
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
            nodes = getattr(graph, "nodes", None)
            if nodes is None:
                continue
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                if node.get("type") != "python_module":
                    continue
                imports_from = node.get("imports_from")
                if not imports_from or not isinstance(imports_from, list):
                    continue
                node_id = node.get("id", "")
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
                                type="depends_on",
                                props={
                                    "import_name": imp_name,
                                    "source_child": child_name,
                                },
                            )
                        )

        return edges
