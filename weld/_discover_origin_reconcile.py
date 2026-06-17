"""Intra-repo origin reconciliation for a single graph (ADR 0042).

A single-repo ``wd discover`` run splits its sources into independent
globs and runs one ``python_callgraph.extract()`` call per glob. Each
call only knows the modules *its own* glob matched (its local
``project_modules`` set), so a call that crosses a glob boundary -- e.g.
a ``weld/strategies/*.py`` symbol calling ``weld.discover.discover`` --
resolves the target module against a set that does not contain it,
classifies it ``external``, and mints the speculative target node with
``origin="external"``. The orchestrator then merges batches with
``dict.update`` (last-batch-wins, ``weld/discover.py``), so that
speculative ``external`` node clobbers the definite ``project`` node the
``weld/*.py`` batch walked. The result is first-party project symbols
mislabelled third-party, which breaks the viz "Hide third-party
dependencies" filter and inflates the external origin count.

ADR 0042 §"Per-language detection rules" (Python) defines the contract
this restores: "Target resolves to a path inside any project file set
discovered **by this run** -> ``project``". The run-level project file
set is exactly the set of ``module`` props on every node already tagged
``origin="project"``. This pass unions those (partitioned by
``props.language`` so a Python project module can never promote a C++
symbol and vice versa) and promotes any same-language ``external``
``symbol`` node whose ``module`` falls in that set back to ``project``.

This is the single-repo analogue of the federation re-tag in
``weld/_discover_federate_origin.py`` (which closes the same gap across
federated child repos). Both promote ``external -> project`` only; they
never demote, and they never touch ``stdlib`` / ``unresolved`` nodes.
The pass is pure with respect to an already-correct graph: when nothing
collides it rewrites nothing and returns ``0``, so byte-identical reruns
stay byte-identical.

It deliberately promotes only ``symbol`` nodes. ``file`` and ``module``
nodes carry an origin stamped by their owning strategy from real
filesystem provenance, not from cross-glob call resolution, so they are
out of scope for this correction.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def _project_modules_by_language(nodes: Dict[str, Any]) -> Dict[str, set]:
    """Return ``{language: {module, ...}}`` for project-tagged nodes.

    Scans every node with ``origin == "project"`` and a non-empty
    string ``props.module`` and groups its module by ``props.language``.
    These are the modules the run actually proved to be first-party.
    """
    by_lang: Dict[str, set] = {}
    for node in nodes.values():
        if not isinstance(node, dict):
            continue
        props = node.get("props")
        if not isinstance(props, dict):
            continue
        if props.get("origin") != "project":
            continue
        language = props.get("language")
        module = props.get("module")
        if not isinstance(language, str) or not language:
            continue
        if not isinstance(module, str) or not module:
            continue
        by_lang.setdefault(language, set()).add(module)
    return by_lang


def reconcile_intra_repo_origins(
    nodes: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
) -> int:
    """Promote ``external`` symbols back to ``project`` within one graph.

    Computes the per-language project module set and, for each ``symbol``
    node tagged ``origin="external"`` whose ``props.module`` is a member
    of the set for the same ``props.language``, rewrites its
    ``props.origin`` to ``"project"``.

    The project module set is the union of two evidence sources:

    1. Every node already tagged ``origin="project"`` (its ``module`` by
       ``language``).
    2. The run-level Python project module set the ``python_callgraph``
       strategy published into ``context["python_project_modules"]`` --
       keyed on the *source file set*, so it still names a module even
       when the orchestrator's last-batch-wins merge clobbered every
       surviving ``project`` node for that module (the single-symbol
       module case).

    Mutates *nodes* in place and returns the number of nodes whose
    ``origin`` was changed. Returns ``0`` -- rewriting nothing -- when
    there are no project modules to match against or no external symbol
    collides, so the canonical graph bytes are unchanged on an
    already-correct input.
    """
    project_by_lang = _project_modules_by_language(nodes)
    if isinstance(context, dict):
        run_python = context.get("python_project_modules")
        if isinstance(run_python, (set, frozenset)) and run_python:
            project_by_lang.setdefault("python", set()).update(run_python)
    if not project_by_lang:
        return 0

    changed = 0
    for node in nodes.values():
        if not isinstance(node, dict):
            continue
        if node.get("type") != "symbol":
            continue
        props = node.get("props")
        if not isinstance(props, dict):
            continue
        if props.get("origin") != "external":
            continue
        language = props.get("language")
        module = props.get("module")
        if not isinstance(language, str) or not isinstance(module, str):
            continue
        if not module:
            continue
        if module in project_by_lang.get(language, frozenset()):
            props["origin"] = "project"
            changed += 1
    return changed


__all__ = ["reconcile_intra_repo_origins"]
