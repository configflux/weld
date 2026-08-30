"""Deferred-import edge marking for ``weld.graph_closure`` (bd 5038-uuxaz.6-repair).

``python_module._extract_imports`` records ``props.deferred_imports`` on a
file node: the subset of ``imports_from`` whose only AST site is a
function/method/class-scoped import -- this repo's own sanctioned
cycle-breaking idiom (ADR 0130; see ``weld/doctor.py``/
``weld/_doctor_staleness.py``, ``ros2_topology.py``/``_ros2_py.py``).
``graph_closure._link_imports`` calls :func:`deferred_names_for` once per
source node and :func:`deferred_edge_props` per emitted ``depends_on`` edge
so ``arch_lint_cycles.rule_no_circular_deps`` can exclude that evidence from
its structural-dependency walk -- a lazy import that breaks a real runtime
cycle should not make the *graph* report a cycle.

Split out as its own leaf (mirrors :mod:`weld._graph_closure_package_origin`)
because ``graph_closure.py`` was already at the 400-line cap; a two-function
helper this small does not warrant inlining back and re-tripping the cap on
the next unrelated change to either module.
"""

from __future__ import annotations


def deferred_names_for(props: dict) -> frozenset[str]:
    """Return the lazy-only import strings recorded on a source node.

    *props* is a file node's ``props`` dict. ``deferred_imports`` is a
    sparse key (only present when non-empty); any other shape (absent,
    wrong type) yields the empty set so callers never need a type guard.
    """
    deferred = props.get("deferred_imports")
    return frozenset(deferred) if isinstance(deferred, list) else frozenset()


def deferred_edge_props(raw_import: str, deferred_names: frozenset[str]) -> dict:
    """``{"deferred": True}`` iff *raw_import* is lazy-only, else ``{}``.

    Sparse by design: merge the result into an edge's props dict so a
    non-lazy ``depends_on`` edge (the common case) stays byte-identical to
    every pre-existing golden fixture.
    """
    return {"deferred": True} if raw_import in deferred_names else {}


__all__ = ["deferred_names_for", "deferred_edge_props"]
