"""Strategy: Conan package-manager extractor (ADR 0057, Wave 1).

Parses two Conan declaration shapes:

- ``conanfile.txt``  -- INI-like sections ``[requires]`` and
  ``[build_requires]``. Every line is a ``name/version`` literal.
- ``conanfile.py``   -- Python AST walk: ``requires`` and
  ``build_requires`` declared as a tuple/list/string literal on a
  class deriving from ``ConanFile`` (or as module-level assignments).

For every dependency line we emit:

- ``package://conan/<name>/<version>`` node (a stable canonical ID
  distinct from the ``package:cpp:`` IDs the CMake strategy emits).
- ``package:cpp:<project> -> depends_on -> package://conan/...`` edge
  on the owning project. ``<project>`` is the directory name of the
  conanfile so the edge resolves cleanly even when no CMakeLists.txt
  is present.

Confidence (ADR 0050):

- ``conanfile.txt``  -> ``definite``: it is a literal config file.
- ``conanfile.py``   parsed-literal -> ``definite``.
- ``conanfile.py``   dynamic / variable / expression  -> ``speculative``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from weld._node_ids import package_id
from weld.strategies._helpers import (
    StrategyResult,
    filter_glob_results,
    should_skip,
)

_STRATEGY = "cpp_conan"

_SECTION_RE = re.compile(r"^\s*\[([a-z_]+)\]\s*$")


def _conan_pkg_id(name: str, version: str | None) -> str:
    """Return ``package://conan/<name>/<version>`` (or ``/unversioned``).

    Slugged via the canonical helpers so ID generation stays
    deterministic and consistent with the rest of the graph.
    """
    if version:
        return f"package://conan/{name}/{version}"
    return f"package://conan/{name}/unversioned"


def _split_dep_literal(literal: str) -> tuple[str, str | None]:
    """Split a Conan ``"name/version"`` literal into its parts.

    Tolerates extra fields (``name/version@user/channel``); returns
    only the name and version, dropping the user/channel suffix --
    they are graph-irrelevant in v1.
    """
    base = literal.split("@", 1)[0].strip()
    if "/" in base:
        name, _, version = base.partition("/")
        return name.strip(), version.strip() or None
    return base, None


def _emit_dep(
    nodes: dict,
    edges: list,
    project_nid: str,
    name: str,
    version: str | None,
    *,
    kind: str,
    confidence: str,
    file_rel: str,
) -> None:
    """Mint a conan node and the depends_on edge from the project."""
    pkg_nid = _conan_pkg_id(name, version)
    nodes.setdefault(
        pkg_nid,
        {
            "type": "package",
            "label": f"conan {name}/{version or '*'}",
            "props": {
                "name": name,
                "version": version or "",
                "source_strategy": _STRATEGY,
                "authority": "external",
                "confidence": "inferred",
                "roles": ["config"],
                "ecosystem": "conan",
            },
        },
    )
    edges.append(
        {
            "from": project_nid,
            "to": pkg_nid,
            "type": "depends_on",
            "props": {
                "source_strategy": _STRATEGY,
                "confidence": confidence,
                "kind": kind,
                "file": file_rel,
            },
        }
    )


def _parse_conanfile_txt(text: str) -> dict[str, list[str]]:
    """Return a section -> list-of-literal map for a ``conanfile.txt``."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = _SECTION_RE.match(line)
        if m:
            current = m.group(1)
            sections.setdefault(current, [])
            continue
        if current is None:
            continue
        sections[current].append(line)
    return sections


def _ast_string_literals(node: ast.AST) -> list[str]:
    """Return the constant-string entries in a list/tuple/str AST node.

    Anything that is not a plain string literal is skipped so the caller
    can flag the call as ``speculative`` when at least one entry is
    non-literal.
    """
    out: list[str] = []
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        out.append(node.value)
        return out
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        for elt in node.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                out.append(elt.value)
    return out


def _has_non_literal(node: ast.AST) -> bool:
    """True if *node* contains non-literal entries (variables, calls)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return False
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        for elt in node.elts:
            if not (
                isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            ):
                return True
        return False
    return True


def _walk_conanfile_py(tree: ast.Module) -> list[tuple[str, str, bool]]:
    """Return ``(kind, literal, speculative)`` triples from a conanfile.py.

    *kind* is ``"requires"`` or ``"build_requires"``. *literal* is a raw
    ``name/version`` string. *speculative* is True when the source
    expression contained at least one non-literal entry (the literal
    list itself may still be partially extractable).
    """
    out: list[tuple[str, str, bool]] = []
    targets = {"requires", "build_requires", "tool_requires"}

    def _add(kind: str, value: ast.AST) -> None:
        speculative = _has_non_literal(value)
        for lit in _ast_string_literals(value):
            out.append((kind, lit, speculative))

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in targets:
                    _add(target.id, node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(
            node.target, ast.Name,
        ):
            if node.target.id in targets and node.value is not None:
                _add(node.target.id, node.value)
        elif isinstance(node, ast.Call):
            # ``self.requires("name/version")`` or
            # ``self.build_requires("name/version")``.
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in targets:
                if node.args:
                    _add(func.attr, node.args[0])
    return out


def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Walk conanfile.* files and emit conan-flavoured dependency edges."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source.get("glob", "")
    excludes = source.get("exclude", [])
    if not pattern:
        return StrategyResult(nodes, edges, discovered_from)

    if "**" in pattern:
        matched = filter_glob_results(root, sorted(root.glob(pattern)))
    else:
        parent = (root / pattern).parent
        if not parent.is_dir():
            return StrategyResult(nodes, edges, discovered_from)
        matched = filter_glob_results(
            root, sorted(parent.glob(Path(pattern).name)),
        )

    for conanfile in matched:
        if not conanfile.is_file():
            continue
        if should_skip(conanfile, excludes, root=root):
            continue

        rel = conanfile.relative_to(root).as_posix()
        try:
            text = conanfile.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        discovered_from.append(rel)

        project = conanfile.parent.name or "conan"
        project_nid = package_id("cpp", project)
        nodes.setdefault(
            project_nid,
            {
                "type": "package",
                "label": project,
                "props": {
                    "name": project,
                    "file": rel,
                    "source_strategy": _STRATEGY,
                    "authority": "canonical",
                    "confidence": "definite",
                    "roles": ["config"],
                },
            },
        )

        if conanfile.name == "conanfile.txt":
            sections = _parse_conanfile_txt(text)
            for kind in ("requires", "build_requires", "tool_requires"):
                for literal in sections.get(kind, []):
                    name, version = _split_dep_literal(literal)
                    if not name:
                        continue
                    _emit_dep(
                        nodes, edges, project_nid, name, version,
                        kind=kind, confidence="definite", file_rel=rel,
                    )
        elif conanfile.name == "conanfile.py":
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            for kind, literal, speculative in _walk_conanfile_py(tree):
                name, version = _split_dep_literal(literal)
                if not name:
                    continue
                _emit_dep(
                    nodes, edges, project_nid, name, version,
                    kind=kind,
                    confidence="speculative" if speculative else "definite",
                    file_rel=rel,
                )

    return StrategyResult(nodes, edges, discovered_from)
