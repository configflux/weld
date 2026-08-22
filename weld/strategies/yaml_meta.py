"""Strategy: CI workflow metadata from YAML files."""

from __future__ import annotations

import re
from pathlib import Path

from weld._node_ids import workflow_id
from weld._rel_path import rel_to_root
from weld.strategies._glob_resolve import resolve_glob
from weld.strategies._helpers import StrategyResult
from weld.strategies._target_ids import target_ids
from weld.strategies._workflow_run_refs import workflow_script_references

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract CI workflow metadata from YAML files."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source["glob"]
    excludes = source.get("exclude", [])
    for yml in resolve_glob(root, pattern, excludes):
        rel_path = rel_to_root(yml, root)
        # Per-file provenance, recorded before the read -- see StrategyResult
        # (bd 8ia5). The old ``parent``-derived entry was ``"./"`` for any
        # root-anchored glob, which marks the whole tree as tracked source.
        discovered_from.append(rel_path)
        try:
            text = yml.read_text(encoding="utf-8")
        except OSError:
            continue
        name = yml.stem
        triggers: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("name:"):
                name = stripped.split(":", 1)[1].strip().strip('"').strip("'")
            elif stripped.startswith("on:"):
                val = stripped.split(":", 1)[1].strip()
                if val:
                    triggers.append(val)
            elif re.match(
                r"^\s+(push|pull_request|workflow_dispatch|schedule):", stripped
            ):
                triggers.append(stripped.strip().rstrip(":"))

        nid = workflow_id(rel_path)
        nodes[nid] = {
            "type": "workflow",
            "label": name,
            "props": {
                "file": rel_path,
                "triggers": triggers,
                "source_strategy": "yaml_meta",
                "authority": "canonical",
                "confidence": "definite",
                "roles": ["config"],
            },
        }
        edges.extend(_invocation_edges(root, nid, rel_path, text))

    return StrategyResult(nodes, edges, discovered_from)


def _invocation_edges(
    root: Path, source_id: str, rel_path: str, text: str
) -> list[dict]:
    """Return ``invokes`` edges for every script named in *text*'s ``run:``
    steps.

    One edge per plausible spelling of each referent -- the same referrer
    contract :func:`weld.strategies.tool_script._invocation_edges` follows:
    this strategy did not mint the target's node, so it cannot know which ID
    class claimed it, and the post-processor's dangling-edge sweep keeps
    whichever spelling resolved (ADR 0106).
    """
    out: list[dict] = []
    for referent in workflow_script_references(root, text):
        for target in target_ids(referent):
            out.append({
                "from": source_id,
                "to": target,
                "type": "invokes",
                "props": {
                    "source_strategy": "yaml_meta",
                    "confidence": "inferred",
                    # ADR 0074: the workflow file this edge was scanned
                    # from, never the invoked target -- so a clean-provenance
                    # edge into a dirtied target survives the incremental
                    # purge instead of falling to the endpoint-membership
                    # floor and never being re-minted (the workflow file is
                    # usually clean when only the script it invokes
                    # changes; bd 57lra).
                    "provenance": {"file": rel_path},
                },
            })
    return out
