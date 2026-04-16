"""Strategy: SQLAlchemy entities and StrEnum definitions."""

from __future__ import annotations

import ast
from pathlib import Path

from weld.strategies._helpers import (
    StrategyResult,
    base_names,
    enum_members,
    extract_columns,
    extract_fks,
    filter_glob_results,
    module_name,
    should_skip,
    tablename,
)

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract SQLAlchemy entities and StrEnum definitions."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source["glob"]
    excludes = source.get("exclude", [])
    domain_dir = (root / pattern).parent
    if not domain_dir.is_dir():
        return StrategyResult(nodes, edges, discovered_from)
    discovered_from.append(str(domain_dir.relative_to(root)) + "/")

    table_to_entity = context.setdefault("table_to_entity", {})
    pending_fk_edges: list = context.setdefault("pending_fk_edges", [])

    for py in filter_glob_results(root, sorted(domain_dir.glob(Path(pattern).name))):
        if py.name.startswith("_") and py.name != "__init__.py":
            continue
        if should_skip(py, excludes):
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except SyntaxError:
            continue
        module = module_name(py, domain_dir)
        rel_path = str(py.relative_to(root))

        for cls_node in (n for n in tree.body if isinstance(n, ast.ClassDef)):
            bases = base_names(cls_node)

            if "Base" in bases:
                tname = tablename(cls_node)
                columns = extract_columns(cls_node)
                fks = extract_fks(cls_node)
                nid = f"entity:{cls_node.name}"
                nodes[nid] = {
                    "type": "entity",
                    "label": cls_node.name,
                    "props": {
                        "module": module,
                        "table": tname,
                        "file": rel_path,
                        "columns": columns,
                        "mixins": [b for b in bases if b != "Base"],
                        "source_strategy": "sqlalchemy",
                        "authority": "canonical",
                        "confidence": "definite",
                        "roles": ["implementation"],
                    },
                }
                if tname:
                    table_to_entity[tname] = nid
                for fk in fks:
                    pending_fk_edges.append(
                        {
                            "from": nid,
                            "to": f"__table__:{fk['table']}",
                            "type": "depends_on",
                            "props": {
                                "fk": fk["ref"],
                                "ondelete": fk.get("ondelete"),
                                "source_strategy": "sqlalchemy",
                                "confidence": "definite",
                            },
                        }
                    )

            elif "StrEnum" in bases:
                members = enum_members(cls_node)
                nid = f"enum:{cls_node.name}"
                nodes[nid] = {
                    "type": "enum",
                    "label": cls_node.name,
                    "props": {
                        "file": rel_path,
                        "members": members,
                        "source_strategy": "sqlalchemy",
                        "authority": "canonical",
                        "confidence": "definite",
                        "roles": ["implementation"],
                    },
                }

    return StrategyResult(nodes, edges, discovered_from)
