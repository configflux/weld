"""Strategy: Top-level classes and functions from Python modules."""

from __future__ import annotations

import ast
from pathlib import Path

from weld.file_index import _module_constant_names
from weld.strategies._helpers import StrategyResult, should_skip

def _extract_imports(tree: ast.Module) -> list[str]:
    """Extract coarse-grained package references from imports.

    Returns deduplicated top-level-ish package strings like
    ``myapp.worker.acquisition`` from
    ``from myapp.worker.acquisition.models import Foo``.
    """
    packages: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                # Keep first 3 dotted parts as coarse ref
                parts = alias.name.split(".")
                packages.add(".".join(parts[:3]))
        elif isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            packages.add(".".join(parts[:3]))
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
    """Build a unique node ID from the relative path.

    If ``id_prefix`` is set (e.g. ``"api"``), the ID becomes
    ``file:api/module_stem``.  For recursive patterns with subdirectories,
    the sub-path is included: ``file:worker/acquisition/service``.

    Without an ``id_prefix``, falls back to ``file:{stem}``.
    """
    p = Path(rel_path)
    stem = p.stem
    if id_prefix:
        # Build path segments between the id_prefix scope and the file
        # e.g. for id_prefix="worker" and rel="services/worker/src/.../acquisition/service.py"
        #   -> "file:worker/acquisition/service"
        parts = p.parts
        # Find the last occurrence of a directory matching the prefix
        # to anchor the relative sub-path.
        anchor_idx = None
        for i, part in enumerate(parts):
            if part == id_prefix:
                anchor_idx = i
        if anchor_idx is not None:
            # Take everything after the anchor directory, excluding the file extension
            sub_parts = list(parts[anchor_idx + 1 :])
            if sub_parts:
                sub_parts[-1] = stem  # replace filename with stem
            else:
                sub_parts = [stem]
            return f"file:{id_prefix}/{'/'.join(sub_parts)}"
        return f"file:{id_prefix}/{stem}"
    return f"file:{stem}"

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
        if py.name.startswith("_") and py.name != "__init__.py":
            continue
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
