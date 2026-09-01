"""Follow a first-party re-export facade to the module that defines the symbol.

``python_callgraph`` resolves a call against the *calling* module's import
table, so ``from weld.contract import validate_graph`` resolves to
``symbol:py:weld.contract:validate_graph``. But ``weld/contract.py`` defines no
``validate_graph``; it re-exports one from ``weld/_graph_doc_validators.py``.
Nothing ever walked that id, so it is minted as a speculative
``make_resolved_target_node`` stub, and every caller that reached the symbol
through the facade hangs off that stub instead of off the definition.

The consequence is not one missing edge. ``weld/contract.py`` is the documented
public import path for the whole validator family, so *every* real consumer
imports through it -- and ``wd callers`` / ``wd impact`` on the defining symbol
answered "no callers" while a control import one line away, taken straight from
its defining module, resolved. The blast radius of a signature change to a
re-exported symbol read as empty. A published facade module is an ordinary
Python layout, not a weld peculiarity, so the same shape hides callers in any
package that has one.

Why closure time
----------------
The strategy walks one glob at a time, and incrementally only the dirty files,
so when it resolves the caller it cannot see the facade's import table. This
pass runs inside ``close_graph``, which ``weld._discover_postprocess`` calls
once per discover over the whole merged node/edge set on both the full and the
incremental path -- the only place with the global view, on both paths.

What is inferred, and what is refused
-------------------------------------
A file node records ``imports_from``: the modules it imports, not the names it
binds. So "the facade re-exports this name" is an inference from *module*
membership, admitted only when all of the following hold.

* The target is a ``make_resolved_target_node`` stub. A module that really
  defines the name was walked, so its symbol carries ``props.file`` and
  ``definite`` confidence and is never a candidate.
* The stub's own module resolves to a ``file:`` node in this graph. Stdlib and
  third-party stubs are the shape that minter exists for and there is no import
  table to follow; only a first-party facade participates.
* Exactly one imported first-party module, at the shallowest level of the walk
  that has any hit at all, holds a non-stub symbol under that qualname. Two
  hits at one level means ``imports_from`` cannot say which import carried the
  name, and the walk refuses -- a stub is a visible "unresolved", a confidently
  wrong edge is not.

Judging ambiguity per level rather than across the whole reachable set is what
keeps the common case: a facade that imports the definer directly *and* some
unrelated module that re-exports the same name is not ambiguous, because Python
binds the name from the facade's own import.

Measured on this repo the rule retargets 44 stubs, every one at the first hop,
and leaves the 18 that are not facades alone -- including the ones where
``python_callgraph`` mis-resolved a method call against a module alias
(``symbol:py:weld._contract_types:get``, ``...:_csharp_syntax:finditer``),
which have no first-party definer to find and so resolve to nothing.

Two limits, both deliberate. An import spelled ``import defining_module`` in the
facade (rather than ``from defining_module import name``) is indistinguishable
here, so a *call site* that imports a name the facade never binds -- code that
already raises ``ImportError`` -- gets an edge to the definition rather than a
stub; the inference cannot be wrong about working code, only about broken code.
And a facade is found by looking its own module name up in the path index, so a
layout where the import spelling drops a source root (``acme.config`` for
``src/acme/config.py``, field-eval N4's shape) is not recognised as a facade at
all -- it keeps its stub, which is the pre-existing answer rather than a worse
one.

Why it undoes itself first
--------------------------
This is the one rule in ``close_graph`` that mutates a *retained* edge rather
than re-deriving from node props, so it cannot be self-correcting the way the
others are for free. It strips its own prior output before re-deriving, which
is what keeps the incremental path equal to a full discover. The mechanism, and
the measurement of which round actually needs it, live with the code that does
it: :mod:`weld._graph_closure_reexport_edges`.
"""

from __future__ import annotations

from weld._graph_closure_modules import python_dotted_module, python_module_index
from weld._graph_closure_reexport_edges import (
    SYMBOL_PREFIX as _SYMBOL_PREFIX,
    collapse_collisions,
    restore_previous_rewrites,
    retarget,
)
from weld.strategies._python_origin import is_resolved_target_stub

#: How many import hops the walk may take before it refuses.
#:
#: Each hop stacks another inference on the last, so the bound is deliberately
#: short of "anything transitively importable". On this repo every real facade
#: resolves at the first hop; the allowance beyond that is for the
#: package-``__init__`` in front of a module in front of the definition, not
#: for a search.
_MAX_DEPTH = 3


def rewrite_reexport_targets(
    nodes: dict[str, dict], edges: list[dict], path_index: dict[str, str],
) -> None:
    """Retarget every edge that reaches its symbol through a first-party facade.

    Undoes its own prior output first so the whole pass is a function of the
    node props plus the edges as the strategies emitted them -- see the module
    docstring for why that ordering is the incremental == full contract and not
    just tidiness.

    Reads *path_index* (repo-relative path -> ``file:`` node) rather than the
    closure's general module index, through the shared Python-only view
    :func:`weld._graph_closure_modules.python_module_index` -- see there for
    why the general index is the wrong one.
    """
    module_index = python_module_index(path_index)
    restore_previous_rewrites(nodes, edges, module_index)
    replacement = _replacements(nodes, module_index)
    if not replacement:
        return
    retarget(edges, replacement)
    collapse_collisions(edges)
    for stub_id in replacement:
        nodes.pop(stub_id, None)


def _replacements(
    nodes: dict[str, dict], module_index: dict[str, str],
) -> dict[str, str]:
    """Map every retargetable stub id to the symbol that actually defines it.

    Walked in whatever order *nodes* holds: the result is a dict keyed by stub
    id whose value is a pure function of the graph, so no ordering can change
    it, and the two discover paths hand ``close_graph`` these nodes in
    different insertion orders.
    """
    out: dict[str, str] = {}
    for stub_id, node in nodes.items():
        if not is_resolved_target_stub(stub_id, node):
            continue
        props = node.get("props")
        module = props.get("module") if isinstance(props, dict) else None
        qualname = props.get("qualname") if isinstance(props, dict) else None
        if not isinstance(module, str) or not isinstance(qualname, str):
            continue
        if not module or not qualname:
            continue
        facade = module_index.get(module)
        if facade is None:
            continue
        target = _follow(facade, qualname, nodes, module_index)
        if target is not None and target != stub_id:
            out[stub_id] = target
    return out


def _follow(
    facade: str,
    qualname: str,
    nodes: dict[str, dict],
    module_index: dict[str, str],
) -> str | None:
    """Walk *facade*'s imports for the one module that defines *qualname*.

    Breadth-first and level-by-level, because the level is what the ambiguity
    rule is judged on -- and judged on distinct *symbols*, not distinct files,
    so two file nodes that read as one module cannot look like a disagreement.
    Visited file nodes are tracked rather than module names, so two spellings of
    one module cannot be walked twice and an import cycle terminates on its
    first repeat.
    """
    seen = {facade}
    frontier = [facade]
    for _hop in range(_MAX_DEPTH):
        hits: list[str] = []
        deeper: list[str] = []
        for file_id in frontier:
            for name in _imports_of(nodes, file_id):
                candidate = module_index.get(name)
                if candidate is None or candidate in seen:
                    continue
                seen.add(candidate)
                found = _definite_symbol(nodes, candidate, qualname)
                if found is None:
                    deeper.append(candidate)
                elif found not in hits:
                    hits.append(found)
        if len(hits) == 1:
            return hits[0]
        if hits or not deeper:
            return None
        frontier = sorted(deeper)
    return None


def _imports_of(nodes: dict[str, dict], file_id: str) -> list[str]:
    """The module names *file_id* imports, deduplicated and sorted."""
    node = nodes.get(file_id)
    props = node.get("props") if isinstance(node, dict) else None
    imports = props.get("imports_from") if isinstance(props, dict) else None
    if not isinstance(imports, list):
        return []
    return sorted({v for v in imports if isinstance(v, str) and v.strip()})


def _definite_symbol(
    nodes: dict[str, dict], file_id: str, qualname: str,
) -> str | None:
    """*file_id*'s own symbol for *qualname*, if it holds a walked one.

    Keyed on the module derived from the file's own path, and only that: symbol
    ids are minted from the defining file's path, never from whatever spelling
    an importer used, so the importer's name is not a second place to look --
    it is a place a match could only be spurious.
    """
    node = nodes.get(file_id)
    props = node.get("props") if isinstance(node, dict) else None
    rel_path = props.get("file") if isinstance(props, dict) else None
    module = python_dotted_module(rel_path) if isinstance(rel_path, str) else ""
    if not module:
        return None
    symbol_id = f"{_SYMBOL_PREFIX}{module}:{qualname}"
    candidate = nodes.get(symbol_id)
    if not isinstance(candidate, dict) or candidate.get("type") != "symbol":
        return None
    if is_resolved_target_stub(symbol_id, candidate):
        return None
    return symbol_id


__all__ = ["rewrite_reexport_targets"]
