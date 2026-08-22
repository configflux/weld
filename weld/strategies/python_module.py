"""Strategy: Top-level classes and functions from Python modules."""

from __future__ import annotations

import ast
from pathlib import Path

from weld._node_ids import file_id as _canonical_file_id
from weld._rel_path import rel_to_root
from weld.file_index import _module_constant_names
from weld.strategies._glob_resolve import resolve_glob_with_provenance
from weld.strategies._helpers import StrategyResult
from weld.strategies._python_anchor import (
    module_exports,
    module_summary,
    yields_file_anchor,
)
from weld.strategies._python_module_incremental import dirty_scoped_matched
from weld.strategies._strategy_failure import note_strategy_failure

def _looks_like_sibling_module(name: str) -> bool:
    """Heuristic: does *name* look like a private sibling module?

    Targets the ``_ros2_py`` / ``_ros2_cpp`` shape -- a single
    leading underscore, all-lowercase, no uppercase letters. The
    convention in this repo (and broadly in Python) is that
    ``_lower_snake_case`` names imported via ``from pkg import _name``
    refer to a sibling module the package author wants to keep
    package-private. Classes (``PascalCase``) and ordinary functions
    (``snake_case`` *without* a leading underscore) are excluded so
    the resolver does not create spurious ``package:python:x.SomeFunc``
    or ``package:python:x.SomeClass`` nodes for the common
    ``from x import some_helper`` / ``from x import SomeClass`` shape.

    This filter gates only the *qualified* ``module.name`` emission
    for ``ImportFrom`` -- the parent ``pkg.mod`` form is always
    emitted regardless. A false negative simply falls back to the
    pre-existing parent-package edge behaviour, which is what shipped
    before this change.
    """
    if not name or name == "*":
        return False
    if not name.startswith("_"):
        return False
    candidate = name.lstrip("_")
    if not candidate:
        return False
    # Lowercase + digits + underscores only; reject any uppercase
    # (classes are PascalCase even when prefixed with ``_``).
    return all(ch.islower() or ch.isdigit() or ch == "_" for ch in candidate)


def _extract_imports(tree: ast.Module) -> list[str]:
    """Extract coarse-grained package references from imports.

    Returns deduplicated top-level-ish package strings like
    ``myapp.worker.acquisition`` from
    ``from myapp.worker.acquisition.models import Foo``.

    Walks the *entire* AST (not just ``tree.body``) so function-local
    and method-local lazy imports surface alongside top-level ones.
    The lazy-import shape -- e.g. ``ros2_topology.extract`` does
    ``from weld.strategies import _ros2_py as _py`` to break a cycle
    -- previously left the imported module with zero inbound
    ``depends_on`` edges (the j5rj symptom that motivated ADR 0041's
    file-anchor-symmetry rule). Walking ``ast.walk`` captures these
    while preserving the existing 3-dot truncation contract -- the
    coarse package form is what ``graph_closure._link_imports``
    expects on ``props.imports_from``.

    For ``from pkg.mod import name`` statements, the parent package
    (``pkg.mod``) is always emitted, and the qualified
    ``pkg.mod.name`` form is *also* emitted when ``name`` matches the
    private-sibling-module convention (leading ``_``, all-lowercase
    body) -- the ``_ros2_py`` / ``_ros2_cpp`` shape. The qualified
    form lets ``_link_imports`` land an edge directly on the sibling
    module's file node, which is exactly what j5rj needs to satisfy
    the file-anchor-symmetry rule. Public symbol imports
    (``from x import some_helper``, ``from x import SomeClass``) keep
    the pre-change behaviour and only emit the parent package, so the
    graph does not gain spurious ``package:python:x.some_helper``
    nodes for every function/class import.
    """
    packages: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                # Keep first 3 dotted parts as coarse ref
                parts = alias.name.split(".")
                packages.add(".".join(parts[:3]))
        elif isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            packages.add(".".join(parts[:3]))
            # Emit qualified ``module.name`` forms only when ``name``
            # looks like a module (heuristic: lower-snake-case with
            # optional leading underscore). This lets the sibling-
            # module shape (``from weld.strategies import _ros2_py``)
            # land an edge on the file node directly while keeping
            # the common ``from x import SomeClass`` case from
            # creating a spurious ``package:python:x.SomeClass`` node.
            for alias in node.names:
                if not _looks_like_sibling_module(alias.name):
                    continue
                qualified = f"{node.module}.{alias.name}".split(".")
                packages.add(".".join(qualified[:3]))
    return sorted(packages)

def _make_node_id(rel_path: str, id_prefix: str) -> str:
    """Build the canonical file-anchor ID for a Python module.

    Per ADR 0041 § Layer 1, file IDs use the full repo-relative POSIX
    path without extension (``file:weld/strategies/python_module``)
    rather than the legacy bare-stem form (``file:python_module``).
    The full-path form is order-independent and unambiguous: two files
    with the same stem in different directories no longer collide.

    The ``id_prefix`` parameter is preserved for backward compatibility
    (other strategies still use it), but for ``python_module`` the
    canonical form already encodes the full path so the prefix is
    folded in only when it would *narrow* the path scope further
    (i.e. when the prefix is not already a path segment).
    """
    if id_prefix:
        # Anchor the rel-path to the named scope when present so the
        # ID still reflects the in-config namespace boundary.
        parts = Path(rel_path).parts
        anchor_idx = None
        for i, part in enumerate(parts):
            if part == id_prefix:
                anchor_idx = i
        if anchor_idx is not None:
            sub_parts = list(parts[anchor_idx + 1 :])
            if sub_parts:
                sub_parts[-1] = Path(sub_parts[-1]).stem
            else:
                sub_parts = [Path(rel_path).stem]
            sub_path = "/".join(sub_parts) if sub_parts else Path(rel_path).stem
            return _canonical_file_id(f"{id_prefix}/{sub_path}")
        return _canonical_file_id(f"{id_prefix}/{Path(rel_path).stem}")
    return _canonical_file_id(rel_path)


def _legacy_stem_file_id(rel_path: str) -> str:
    """Return the pre-ADR-0041 ``file:<stem>`` form for *rel_path*.

    The legacy ID was the bare module stem -- e.g.
    ``file:python_module`` for ``weld/strategies/python_module.py``.
    Recorded under ``aliases`` for one minor version per ADR 0041 so
    external transcripts that pasted the old form still resolve via
    the alias-aware lookup.
    """
    return f"file:{Path(rel_path).stem}"

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract top-level classes and functions from Python modules."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source["glob"]
    excludes = source.get("exclude", [])
    package_id = source.get("package", "")
    id_prefix = source.get("id_prefix", "")

    matched, dirs = resolve_glob_with_provenance(root, pattern, excludes)
    discovered_from.extend(dirs)

    if not matched:
        return StrategyResult(nodes, edges, discovered_from)

    # ADR 0084: incremental dirty-scoping. When the orchestrator hands a
    # dirty-file hint (``context[INCREMENTAL_HINT_KEY]``), parse only the
    # dirty subset of this glob instead of re-parsing every sibling to keep
    # one. ``python_module`` has zero cross-file state, so the narrowed node
    # set is byte-identical to a full parse's -- the orchestrator discards
    # the non-dirty siblings' nodes anyway (``discover.py`` merge). ``hint is
    # None`` (full discover + every non-incremental caller) returns ``matched``
    # unchanged, preserving whole-glob behaviour byte-for-byte.
    parse_files = dirty_scoped_matched(matched, root, context)

    for py in parse_files:
        try:
            source_text = py.read_text(encoding="utf-8")
            tree = ast.parse(source_text, filename=str(py))
        except (OSError, UnicodeDecodeError, SyntaxError):
            # bd hch4: a file we could not parse is a *failure*, not this
            # strategy deciding the file anchors nothing -- ``yields_file_anchor``
            # below is that decision. Recording a parse failure as a decision
            # exempted it from the ADR 0008 per-file repair for good, so the
            # day weld's parser grows to accept the file, nothing would ever
            # re-read it.
            #
            # bd pt38: the same holds for a file we could not *read*, which is
            # why this catches what ``python_callgraph`` catches rather than
            # ``SyntaxError`` alone. ``read_text`` raises ``OSError``, never
            # ``SyntaxError``, so a file removed between the walk and the read
            # used to take the whole run down with it. That window is never
            # zero -- the run walks once and reads later (``build_file_hashes``
            # at run start, strategies after), and the per-run glob memo
            # (bd cjij) widens it to the length of the run -- so a concurrent
            # editor, worktree switch, or CI checkout is enough to hit it.
            # A vanished file is the repairable kind of failure by definition:
            # if it comes back the next pass anchors it, and if it does not it
            # leaves ``state.files`` and stops being asked about.
            note_strategy_failure(context, [rel_to_root(py, root)])
            continue
        rel_path = rel_to_root(py, root)
        # The export collection and the "does this file anchor at all?"
        # rule both live in ``_python_anchor`` so ``python_package`` can
        # ask the same question without restating it (issue ``ddsy``).
        # Restating it is the drift ADR 0041 § Layer 3 exists to prevent.
        exports = module_exports(tree)
        if not yields_file_anchor(py.name, exports):
            continue

        nid = _make_node_id(rel_path, id_prefix)
        newlines = source_text.count("\n")
        line_count = newlines + (1 if source_text and not source_text.endswith("\n") else 0)
        imports_from = _extract_imports(tree)
        # Module-level constants (UPPER_CASE / _UPPER_CASE). These are
        # the residual "what does this module own" surface that
        # ``exports`` (classes + public functions) does not cover. They
        # feed ``wd query`` via ``query_index.node_tokens`` -- bounded
        # and ReDoS-safe; see ``weld.file_index`` for the cap rationale.
        # Sorted + deduplicated so the graph artifact is canonical
        # (ADR 0012 §3).
        constants = sorted(set(_module_constant_names(tree)))
        # bd ph1g: the opening paragraph of the module docstring -- the one
        # sentence the author wrote to say what this module is. Read here for
        # the same reason ``constants`` is: it feeds ``wd query`` through
        # ``query_index.node_tokens``. Before this, discovery kept a module's
        # exports and threw its summary away, so the graph's only prose came
        # from an enrichment pass covering 1.96% of nodes -- which is why a
        # query for a filename stated plainly in ``weld/serializer.py``'s first
        # line matched nothing at all. Always emitted, empty when there is no
        # docstring, so the node shape does not vary with the source.
        summary = module_summary(tree)

        # ADR 0041 § Migration: record the pre-rename ``file:<stem>``
        # form on ``aliases`` when the canonical full-path ID differs
        # from the bare-stem legacy ID. ``[]`` for the (rare) case
        # where the legacy and canonical forms collapse to the same
        # string (a single-segment module at the repo root).
        legacy_nid = _legacy_stem_file_id(rel_path)
        aliases = [legacy_nid] if legacy_nid != nid else []
        nodes[nid] = {
            "type": "file",
            "label": py.stem,
            "props": {
                "file": rel_path,
                "exports": exports,
                "constants": constants,
                "summary": summary,
                "imports_from": imports_from,
                "line_count": line_count,
                "source_strategy": "python_module",
                "authority": "derived",
                "confidence": "definite",
                "roles": ["implementation"],
                "aliases": aliases,
                # ADR 0042: project files are always project-origin --
                # the strategy only walks files inside the discovered
                # glob, which by construction is the project tree.
                "origin": "project",
            },
        }
        if package_id:
            edges.append(
                {
                    "from": package_id,
                    "to": nid,
                    "type": "contains",
                    "props": {
                        "source_strategy": "python_module",
                        "confidence": "definite",
                    },
                }
            )

    return StrategyResult(nodes, edges, discovered_from)
