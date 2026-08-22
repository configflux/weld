"""Strategy: Worker stage exports from __init__.py files."""

from __future__ import annotations

import ast
from pathlib import Path

from weld._rel_path import rel_to_root
from weld.strategies._helpers import StrategyResult, extract_all, filter_glob_results
from weld.strategies._strategy_failure import note_strategy_failure

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract worker stage exports from __init__.py files."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source["glob"]
    # ``worker_dir`` drives the traversal below; it is not provenance. The
    # old ``worker_dir``-derived entry was ``"./"`` for a root-anchored glob,
    # which marks the whole tree as tracked source (bd 8ia5).
    worker_dir = (root / pattern).parent
    if not worker_dir.is_dir():
        return StrategyResult(nodes, edges, discovered_from)

    for stage_dir in filter_glob_results(root, sorted(worker_dir.iterdir())):
        if not stage_dir.is_dir():
            continue
        init_py = stage_dir / "__init__.py"
        if not init_py.exists():
            continue
        rel_path = rel_to_root(init_py, root)
        # Recorded before the parse: the file is this strategy's input even
        # when it yields no stage node (see StrategyResult).
        discovered_from.append(rel_path)
        try:
            tree = ast.parse(
                init_py.read_text(encoding="utf-8"), filename=str(init_py)
            )
        except (OSError, UnicodeDecodeError, SyntaxError):
            # bd o642, applying bd pt38's fix here: ``read_text`` raises
            # ``OSError`` -- never ``SyntaxError`` -- so guarding the parse
            # alone let an unreadable ``__init__.py`` abort the entire run.
            # The ``exists()`` above is a check-then-use: it narrows the window
            # for a file that vanishes to the gap between the two syscalls, but
            # nothing holds the file still in between, so this guard is what
            # closes it. ``UnicodeDecodeError`` is a ``ValueError`` (a latin-1
            # module is legal Python), so ``OSError`` alone would not do.
            # Recorded as a *failure*, unlike the ``exists()`` skip above,
            # which is this strategy deciding a directory is not a stage: a
            # decision is keyed on the path and exempts the file from the
            # ADR 0008 per-file repair for good (bd hch4).
            note_strategy_failure(context, [rel_path])
            continue
        exports = extract_all(tree)
        stage_name = stage_dir.name
        nid = f"stage:{stage_name}"
        nodes[nid] = {
            "type": "stage",
            "label": stage_name.title(),
            "props": {
                "file": rel_path,
                "exports": exports,
                "source_strategy": "worker_stage",
                "authority": "canonical",
                "confidence": "definite",
                "roles": ["implementation"],
            },
        }

    return StrategyResult(nodes, edges, discovered_from)
