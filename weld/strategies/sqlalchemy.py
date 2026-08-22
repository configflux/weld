"""Strategy: SQLAlchemy entities and StrEnum definitions."""

from __future__ import annotations

import ast
from pathlib import Path

from weld._rel_path import rel_to_root
from weld.strategies._glob_resolve import resolve_glob
from weld.strategies._helpers import (
    StrategyResult,
    base_names,
    enum_members,
    extract_columns,
    extract_fks,
    module_name,
    tablename,
)
from weld.strategies._strategy_failure import note_strategy_failure

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract SQLAlchemy entities and StrEnum definitions."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source["glob"]
    excludes = source.get("exclude", [])
    # ``domain_dir`` is passed to ``module_name`` below; it is not provenance.
    # The old ``domain_dir``-derived provenance entry was ``"./"`` for a
    # root-anchored glob, which marks the whole tree as tracked source
    # (bd 8ia5). Its ``is_dir()`` early-return is gone (bd t06t): for a ``**``
    # pattern the parent is a literal ``pkg/**`` path that is never a
    # directory, so the guard made recursive globs emit nothing at all --
    # and for a flat glob it only repeated what the walker already does.
    domain_dir = (root / pattern).parent

    table_to_entity = context.setdefault("table_to_entity", {})
    pending_fk_edges: list = context.setdefault("pending_fk_edges", [])

    for py in resolve_glob(root, pattern, excludes):
        if py.name.startswith("_") and py.name != "__init__.py":
            continue
        rel_path = rel_to_root(py, root)
        # Recorded before the parse: a file that declares no entity today
        # must still be re-read once someone adds one (see StrategyResult).
        discovered_from.append(rel_path)
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except (OSError, UnicodeDecodeError, SyntaxError):
            # bd o642, applying bd pt38's fix here: ``read_text`` raises
            # ``OSError`` -- never ``SyntaxError`` -- so guarding the parse
            # alone let a file that vanished between the walk above and this
            # read abort the entire run. The window is the whole run, not the
            # loop: ``walk_glob`` is served from the per-run glob memo
            # (bd cjij), so the listing this loop reads was taken when the run
            # began. ``UnicodeDecodeError`` is a ``ValueError``, so widening to
            # ``OSError`` alone would still abort on non-UTF-8 bytes. Recorded
            # as a *failure*, not as this strategy deciding the file declares
            # no entity: a decision is keyed on the path and exempts the file
            # from the ADR 0008 per-file repair for good (bd hch4).
            note_strategy_failure(context, [rel_path])
            continue
        module = module_name(py, domain_dir)

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
