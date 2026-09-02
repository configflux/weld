"""Strategy: Pydantic BaseModel contracts and contract-level enums."""

from __future__ import annotations

import ast
from pathlib import Path

from weld._rel_path import rel_to_root
from weld.strategies._glob_resolve import resolve_glob
from weld.strategies._helpers import (
    StrategyResult,
    enum_members,
    extract_contracts,
    inherits,
)
from weld.strategies._strategy_failure import note_strategy_failure

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract Pydantic BaseModel contracts and contract-level enums."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source["glob"]
    excludes = source.get("exclude", [])

    # bd b9xgd: this used to resolve its own glob -- ``(root / pattern).parent``,
    # an ``is_dir()`` early-return, then one directory's worth of ``glob()``.
    # That is the copy ADR 0112 says is gone, kept here because this strategy
    # was never migrated. It made any ``**`` pattern *and* any wildcard in a
    # directory segment (``api/*/contracts/*.py``) resolve to nothing at all,
    # since both give a parent that is a literal path and never a directory.
    for py in resolve_glob(root, pattern, excludes):
        if py.name.startswith("_"):
            continue
        rel_path = rel_to_root(py, root)
        # Recorded before the parse: a file that yields no contract today
        # must still be re-read once someone adds one (see StrategyResult).
        discovered_from.append(rel_path)
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except (OSError, UnicodeDecodeError, SyntaxError):
            # bd o642, applying bd pt38's fix here: ``read_text`` raises
            # ``OSError`` -- never ``SyntaxError`` -- so guarding the parse
            # alone let a file that vanished between the listing above and this
            # read abort the entire run. ``UnicodeDecodeError`` is a
            # ``ValueError``, so widening to ``OSError`` alone would still
            # abort on non-UTF-8 bytes. Recorded as a *failure*, not as this
            # strategy deciding the file holds no contract: a decision is keyed
            # on the path and exempts the file from the ADR 0008 per-file
            # repair for good, so one that came back unchanged would never be
            # re-read (bd hch4).
            note_strategy_failure(context, [rel_path])
            continue
        for contract in extract_contracts(tree):
            nid = f"contract:{contract['name']}"
            nodes[nid] = {
                "type": "contract",
                "label": contract["name"],
                "props": {
                    "file": rel_path,
                    "fields": contract["fields"],
                    "description": contract["docstring"],
                    "source_strategy": "pydantic",
                    "authority": "canonical",
                    "confidence": "definite",
                    "roles": ["implementation"],
                },
            }
        # Also extract contract-level StrEnum definitions
        for cls_node in (n for n in tree.body if isinstance(n, ast.ClassDef)):
            if inherits(cls_node, "StrEnum"):
                members = enum_members(cls_node)
                nid = f"enum:{cls_node.name}"
                if nid not in nodes:
                    nodes[nid] = {
                        "type": "enum",
                        "label": cls_node.name,
                        "props": {
                            "file": rel_path,
                            "members": members,
                            "source_strategy": "pydantic",
                            "authority": "canonical",
                            "confidence": "definite",
                            "roles": ["implementation"],
                        },
                    }

    return StrategyResult(nodes, edges, discovered_from)
