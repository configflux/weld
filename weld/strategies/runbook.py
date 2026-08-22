"""Strategy: Runbook extraction as first-class graph objects.

Reads markdown runbooks and emits ``runbook`` type nodes (rather than
generic ``doc`` nodes).  Extracts the title from the first H1 heading
and creates ``documents`` edges to worker stages or services that the
runbook covers (inferred from filename conventions).

"""

from __future__ import annotations

from pathlib import Path

from weld._rel_path import rel_to_root
from weld.strategies._glob_resolve import resolve_glob
from weld.strategies._helpers import StrategyResult
from weld.strategies._markdown_fence import iter_headings

# -- Service / stage association mapping ------------------------------------

#: Map filename fragments to graph node IDs for edge creation.
_SERVICE_ASSOCIATIONS: dict[str, str] = {
    "acquisition": "stage:acquisition",
    "extraction": "stage:extraction",
    "matching": "stage:matching",
    "notification": "stage:notification",
    "api": "service:api",
    "web": "service:web",
    "worker": "service:worker",
}

def _extract_title(text: str) -> str | None:
    """Extract the first H1 heading from markdown text.

    Fenced blocks are skipped (bd ve41): "first ``#`` line" is the same scan
    the doc strategy runs, and a runbook that opens with a sample command
    block would otherwise be titled after a shell comment.
    """
    for _index, level, heading in iter_headings(text):
        if level == 1:
            return heading
    return None

def _infer_associations(stem: str) -> list[str]:
    """Infer associated graph node IDs from the runbook filename stem."""
    associations: list[str] = []
    stem_lower = stem.lower()
    for fragment, nid in _SERVICE_ASSOCIATIONS.items():
        if fragment in stem_lower:
            associations.append(nid)
    return associations

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract runbook nodes from markdown files."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source.get("glob", "")
    excludes = source.get("exclude", [])

    if not pattern:
        return StrategyResult(nodes, edges, discovered_from)

    for md in resolve_glob(root, pattern, excludes):
        # Always skip README.md
        if md.name == "README.md":
            continue

        rel_path = rel_to_root(md, root)
        discovered_from.append(rel_path)

        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue

        # Extract title from H1 heading, fall back to filename
        title = _extract_title(text)
        if not title:
            title = md.stem.replace("_", " ").title()

        nid = f"runbook:{md.stem}"
        nodes[nid] = {
            "type": "runbook",
            "label": title,
            "props": {
                "file": rel_path,
                "doc_kind": "runbook",
                "source_strategy": "runbook",
                "authority": "canonical",
                "confidence": "definite",
                "roles": ["doc"],
            },
        }

        # Create edges to associated services/stages
        for assoc_nid in _infer_associations(md.stem):
            edges.append({
                "from": nid,
                "to": assoc_nid,
                "type": "documents",
                "props": {
                    "source_strategy": "runbook",
                    "confidence": "inferred",
                },
            })

    return StrategyResult(nodes, edges, discovered_from)
