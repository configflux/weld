"""Helpers for emitting ``inherits`` edges from the python call-graph strategy.

ADR 0064 § "The eight criteria" / criterion 2 requires ``inherits`` /
``implements`` edges to originate at the most-specific symbol node
(class or method) and to land on a known node in the graph. The python
strategy stack previously emitted neither edge type, leaving criterion 2
as ``stub`` on every python corpus. This helper plugs that gap for the
``inherits`` half (python has no nominal interface concept, so no
``implements`` shape is meaningful here).

Centralising the resolution + emission keeps ``python_callgraph.py``
under the repo line-count cap and mirrors the established
``_python_origin.py`` sister module.

Resolution mirrors the call-target resolver in
:class:`weld.strategies.python_callgraph._CallGraphVisitor`:

* Bare base ``class B(A)`` resolves to a sibling ``class A:`` defined
  in the same module via the visitor's own symbols dict.
* Imported base ``class B(Base)`` resolves via the module-level
  import table when ``Base`` is bound by ``from <module> import Base``.
* Bases that resolve nowhere fall back to the shared
  ``symbol:unresolved:<name>`` sentinel, mirroring the call-edge
  unresolved path so the graph stays referentially closed under the
  ADR 0042 origin contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from weld.strategies._python_origin import (
    is_stdlib_module,
    make_resolved_target_node,
    make_sentinel_node,
    module_from_symbol_id,
    origin_for_resolved,
    origin_for_sentinel,
)

if TYPE_CHECKING:
    from weld.strategies.python_callgraph import _CallGraphVisitor


# Reuse the prefix from python_callgraph to keep the sentinel IDs
# identical across the two emission paths. Imported lazily inside
# helpers below so a typo in either module surfaces immediately rather
# than producing a divergent ID shape.
def _unresolved_prefix() -> str:
    from weld.strategies.python_callgraph import UNRESOLVED_PREFIX

    return UNRESOLVED_PREFIX


def _resolve_base(
    base_name: str,
    *,
    visitor: "_CallGraphVisitor",
) -> tuple[str, bool, str]:
    """Resolve a base name to ``(target_id, resolved, resolution)``.

    Returns the same triple shape the call-target resolver emits so the
    edge-emission code can use one downstream payload for both edge
    types.
    """
    from weld.strategies.python_callgraph import _symbol_id, _unresolved_id

    # 1. Same-module sibling class -- ``visitor.symbols`` collects every
    #    qualname seen during the walk; a top-level class name appears
    #    as its bare ``name`` key. Methods cannot be used as bases.
    if base_name in visitor.symbols:
        return _symbol_id(visitor.module_path, base_name), True, "local"
    # 2. Import-table hit -- ``from <module> import Base [as alias]``
    #    binds ``Base`` (or ``alias``) -> ``(module, "Base")``. A module
    #    alias entry (``import foo.bar as mod``) carries an empty
    #    ``attr`` slot, which signals "no usable target" for inheritance
    #    just as it does for calls.
    if base_name in visitor.import_table:
        module, attr = visitor.import_table[base_name]
        if attr:
            resolution = "stdlib" if is_stdlib_module(module) else "import"
            return _symbol_id(module, attr), True, resolution
    # 3. Fall through to the shared unresolved sentinel.
    return _unresolved_id(base_name), False, "unresolved"


def emit_inherits_edges(
    *,
    visitor: "_CallGraphVisitor",
    module_path: str,
    rel_path: str,
    project_modules: frozenset[str],
    nodes: dict[str, dict],
    edges: list[dict],
) -> None:
    """Emit one ``inherits`` edge per declared base for every class.

    Mutates *nodes* (to lazily mint sentinel / resolved target nodes for
    referential closure) and *edges* (to append one edge per base).
    Mirrors the ``calls`` edge shape so consumers can apply uniform
    filters across the two edge types.
    """
    from weld.strategies.python_callgraph import _symbol_id

    for class_qual, base_names in visitor.class_bases.items():
        if not base_names:
            # No explicit bases -- ``class Foo:`` produces no edge; the
            # implicit ``inherits -> object`` shape carries no
            # extraction signal and is filtered upstream.
            continue
        from_id = _symbol_id(module_path, class_qual)
        seen: set[tuple[str, bool]] = set()
        for raw in base_names:
            target_id, resolved, resolution = _resolve_base(raw, visitor=visitor)
            key = (target_id, resolved)
            if key in seen:
                continue
            seen.add(key)
            if target_id.startswith(_unresolved_prefix()):
                nodes.setdefault(
                    target_id,
                    make_sentinel_node(
                        target_id, resolution, origin_for_sentinel(resolution)
                    ),
                )
            else:
                target_module = module_from_symbol_id(target_id)
                nodes.setdefault(
                    target_id,
                    make_resolved_target_node(
                        target_id,
                        origin_for_resolved(target_module, project_modules),
                    ),
                )
            edges.append(
                {
                    "from": from_id,
                    "to": target_id,
                    "type": "inherits",
                    "props": {
                        "source_strategy": "python_callgraph",
                        "confidence": "definite" if resolved else "speculative",
                        "resolved": resolved,
                        "raw": raw,
                        "resolution": resolution,
                        "provenance": {
                            "file": rel_path,
                            "line": visitor.symbols.get(
                                class_qual, {}
                            ).get("line", 0),
                        },
                    },
                }
            )


__all__ = [
    "emit_inherits_edges",
]
