"""Helpers for tagging ``props.origin`` on Python-strategy nodes (ADR 0042).

Centralises the four-way origin classification used by
``python_callgraph`` so the strategy file stays focused on AST walking
and call resolution. The helpers here are pure: no I/O, no module
imports beyond ``sys`` for the stdlib list and ``builtins`` for the
builtin-name set.

See ``docs/adrs/0042-graph-node-origin.md`` §"Per-language detection
rules" (Python) for the contract.
"""

from __future__ import annotations

import builtins
import sys
from pathlib import Path
from typing import Any, Callable

from weld._rel_path import rel_to_root

#: Sentinel ID prefix for a call site whose target name could not be
#: resolved against the module's imports or local definitions. Kept stable
#: so consumers can filter / rank these distinctly from resolved targets.
#:
#: Lives here with the id READERS below (``module_from_symbol_id``,
#: ``qualname_from_symbol_id``) and the node minters that stamp them, rather
#: than in ``python_callgraph``, so an emitter can mint an id without
#: importing the strategy that dispatches into it. ``python_callgraph``
#: re-exports both minters under their historical names, so this is one
#: definition, not a second one.
UNRESOLVED_PREFIX = "symbol:unresolved:"

#: Names that resolve to a Python built-in via implicit lookup.
_BUILTIN_NAMES: frozenset[str] = frozenset(dir(builtins))
#: Top-level module names exported by the Python standard library.
_STDLIB_MODULES: frozenset[str] = frozenset(
    getattr(sys, "stdlib_module_names", frozenset())
)


def is_stdlib_module(module: str) -> bool:
    """Return True if *module*'s first dotted segment is in the stdlib list."""
    if not module:
        return False
    root = module.split(".", 1)[0]
    return root in _STDLIB_MODULES


def is_builtin_name(name: str) -> bool:
    """Return True if *name* would resolve to a Python built-in."""
    return name in _BUILTIN_NAMES


def project_module_set(
    root: Path,
    matched: list[Path],
    *,
    module_dotted_path: Callable[[str], str],
) -> frozenset[str]:
    """Return the dotted module paths for project-local files in *matched*.

    Mirrors the ``module_dotted_path`` derivation used by the main
    extraction loop so the membership test is consistent with the IDs
    the strategy actually mints (ADR 0042 Python rule 3).
    """
    out: set[str] = set()
    for py in matched:
        try:
            rel_path = rel_to_root(py, root)
        except ValueError:
            continue
        dotted = module_dotted_path(rel_path)
        if dotted:
            out.add(dotted)
    return frozenset(out)


def symbol_id(module_path: str, qualname: str) -> str:
    """Return a stable id for a Python symbol.

    Symbol IDs preserve their original module-path and qualname casing
    (ADR 0041 § Migration plan does not list ``symbol:py:*`` in the rename
    table, and lowercasing class qualnames would silently rewrite every
    symbol edge in the existing graph). The canonical slug rule applies to
    ID *prefixes* and human-name segments; qualnames are program
    identifiers and stay verbatim.
    """
    return f"symbol:py:{module_path}:{qualname}"


def unresolved_id(name: str) -> str:
    """Return the shared sentinel id for an unresolvable *name*."""
    return f"{UNRESOLVED_PREFIX}{name}"


def module_from_symbol_id(symbol_id_value: str) -> str:
    """Extract the dotted-module portion of a ``symbol:py:<mod>:<qual>`` id."""
    parts = symbol_id_value.split(":", 3)
    if len(parts) >= 3 and parts[0] == "symbol" and parts[1] == "py":
        return parts[2]
    return ""


def qualname_from_symbol_id(symbol_id_value: str) -> str:
    """Extract the bare ``<qualname>`` portion of a ``symbol:py:<mod>:<qual>`` id.

    Symmetric to :func:`module_from_symbol_id`: splits with ``maxsplit=3`` so
    the qualname (which may itself contain dots, e.g. ``Class.method``) is
    returned verbatim and the module is *not* leaked into it. A naive
    ``split(":", 2)[-1]`` would yield ``<mod>:<qual>`` -- the malformed label
    that clobbered file-bearing project nodes on a full discover. Falls back
    to the trailing segment for ids that are not a well-formed python symbol.
    """
    parts = symbol_id_value.split(":", 3)
    if len(parts) == 4 and parts[0] == "symbol" and parts[1] == "py":
        return parts[3]
    return symbol_id_value.split(":", 2)[-1]


def origin_for_resolved(module: str, project_modules: frozenset[str]) -> str:
    """Origin for a resolved (non-sentinel) Python target.

    Per ADR 0042: stdlib first (``sys.stdlib_module_names`` membership
    of the first dotted segment), then project (any dotted-path match
    against this run's project file set), else external.
    """
    if not module:
        return "external"
    if is_stdlib_module(module):
        return "stdlib"
    if module in project_modules:
        return "project"
    return "external"


def origin_for_sentinel(resolution: str) -> str:
    """Origin for an unresolved-sentinel node.

    ``builtin``-tagged sentinels are stdlib (Python's implicit-lookup
    builtins live in ``builtins``); everything else is unresolved.
    """
    return "stdlib" if resolution == "builtin" else "unresolved"


def make_resolved_target_node(target_id: str, origin: str) -> dict[str, Any]:
    """Build the node payload for a resolved cross-module call target.

    Used when the call resolves to a symbol the python_callgraph strategy
    did not also walk (e.g. a stdlib or external import). The node is
    speculative (we never actually parsed its definition) but carries
    the origin tag so consumers can filter it consistently.

    The ``label``/``qualname`` are the bare symbol name (see
    :func:`qualname_from_symbol_id`); we do *not* leak the ``module:qualname``
    composite, so this speculative node's identifying shape matches a
    same-glob definite node's. ``props.file`` is intentionally absent: a
    cross-glob target's defining file is unknown at single-glob mint time.
    When another source entry did walk that definition, the orchestrator's
    merge keeps its definite node rather than this stub (ADR 0103), so the
    file is never lost in the first place; when nothing walked it, there is no
    file to know and the stub stands.
    """
    qualname = qualname_from_symbol_id(target_id)
    module = module_from_symbol_id(target_id)
    return {
        "type": "symbol",
        "label": qualname,
        "props": {
            "module": module,
            "qualname": qualname,
            "language": "python",
            "source_strategy": "python_callgraph",
            "authority": "derived",
            "confidence": "speculative",
            "roles": ["implementation"],
            "origin": origin,
        },
    }


def is_resolved_target_stub(node_id: str, node: dict[str, Any]) -> bool:
    """Return True iff *node_id*/*node* is a :func:`make_resolved_target_node` output.

    Lives beside the minter because two readers need it and a five-condition
    fingerprint kept in two places drifts: :mod:`weld._discover_resolved_stub_purge`
    (which drops such a stub once its last inbound edge is gone) and
    :mod:`weld._graph_closure_reexport` (which retargets the edges pointing at
    one when the module it names merely re-exports the symbol).

    Keyed on the node's own props, never on id shape. Unlike the
    ``symbol:unresolved:`` sentinel, whose id is disjoint from every real id by
    construction, this id shape (``symbol:py:<module>:<qual>``) is exactly what
    a genuinely-walked, ``definite`` symbol uses, so a rule keyed on shape
    alone would match real symbols. That prefix is excluded here for the same
    reason in reverse: ``make_sentinel_node`` stamps an identical props shape
    beneath it, and the purge rule that owns it is a different rule.

    A real, definite ``python_callgraph`` symbol always sets ``props.file`` at
    mint time, so "no ``props.file``, ``speculative`` confidence" cannot
    describe an actually-walked symbol. *node* reaches here from strategy
    plugins, including project-local overrides under ``.weld/strategies/``, so
    its shape is read defensively rather than trusted.
    """
    if not isinstance(node_id, str) or node_id.startswith("symbol:unresolved:"):
        return False
    if node.get("type") != "symbol":
        return False
    props = node.get("props")
    if not isinstance(props, dict) or props.get("file"):
        return False
    return (
        props.get("confidence") == "speculative"
        and props.get("authority") == "derived"
        and props.get("source_strategy") == "python_callgraph"
    )


def make_sentinel_node(target_id: str, resolution: str, origin: str) -> dict[str, Any]:
    """Build the node payload for an unresolved-prefix sentinel."""
    qualname = target_id.split(":", 2)[-1]
    return {
        "type": "symbol",
        "label": qualname,
        "props": {
            "module": "",
            "qualname": qualname,
            "language": "python",
            "resolved": False,
            "resolution": resolution,
            "source_strategy": "python_callgraph",
            "authority": "derived",
            "confidence": "speculative",
            "roles": ["implementation"],
            "origin": origin,
        },
    }


__all__ = [
    "UNRESOLVED_PREFIX",
    "is_builtin_name",
    "is_resolved_target_stub",
    "is_stdlib_module",
    "make_resolved_target_node",
    "make_sentinel_node",
    "module_from_symbol_id",
    "origin_for_resolved",
    "origin_for_sentinel",
    "project_module_set",
    "qualname_from_symbol_id",
    "symbol_id",
    "unresolved_id",
]
