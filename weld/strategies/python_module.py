"""Strategy: Top-level classes and functions from Python modules."""

from __future__ import annotations

import ast
from pathlib import Path

from weld._node_ids import file_id as _canonical_file_id
from weld.file_index import _module_constant_names
from weld.strategies._helpers import StrategyResult, should_skip

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

def _resolve_glob(
    root: Path,
    pattern: str,
    excludes: list[str] | None = None,
) -> tuple[list[Path], list[str]]:
    """Resolve a glob pattern that may contain ``**``.

    Returns ``(matched_files, discovered_from_dirs)``. Uses the shared
    prune-during-descent walker so excluded subtrees (``.cache/bazel``,
    ``node_modules``, and user *excludes*) are never visited.
    """
    from weld.glob_match import walk_glob

    files: list[Path] = []
    dirs: set[str] = set()

    if "**" in pattern:
        for py in walk_glob(root, pattern, excludes=excludes):
            files.append(py)
            dirs.add(str(py.parent.relative_to(root)) + "/")
    else:
        parent = (root / pattern).parent
        if not parent.is_dir():
            return [], []
        for py in walk_glob(root, pattern, excludes=excludes):
            files.append(py)
        dirs.add(str(parent.relative_to(root)) + "/")

    return files, sorted(dirs)

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

    matched, dirs = _resolve_glob(root, pattern, excludes)
    discovered_from.extend(dirs)

    if not matched:
        return StrategyResult(nodes, edges, discovered_from)

    for py in matched:
        # ADR 0041 § Layer 3: the ``_*``-skip rule was a unilateral
        # decision in this strategy that drifted from the paired
        # ``python_callgraph`` strategy and produced file anchors with
        # no inbound edges (the ``_ros2_py`` symptom). Both strategies
        # now defer to the config-driven ``should_skip`` so the pair
        # processes the same file set.
        if should_skip(py, excludes, root=root):
            continue
        try:
            source_text = py.read_text(encoding="utf-8")
            tree = ast.parse(source_text, filename=str(py))
        except SyntaxError:
            continue
        rel_path = str(py.relative_to(root))
        exports: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                exports.append(node.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_"):
                    exports.append(node.name)
        if not exports and py.name == "__init__.py":
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
                "imports_from": imports_from,
                "line_count": line_count,
                "source_strategy": "python_module",
                "authority": "derived",
                "confidence": "definite",
                "roles": ["implementation"],
                "aliases": aliases,
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
