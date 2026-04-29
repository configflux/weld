"""Graph diff between discovery runs.

Compares the current ``.weld/graph.json`` against a previous snapshot
(``graph-previous.json``) saved before the last discovery run.  Produces
both human-readable and machine-readable (JSON) output.

The previous snapshot is created by the discovery orchestrator
(``weld.discover``) just before overwriting the graph.

CLI surface:
    wd diff                # human-readable summary
    wd diff --json         # stable JSON contract for agents

MCP surface:
    weld_diff()              # returns the JSON diff dict
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Diff computation (pure, no I/O)
# ---------------------------------------------------------------------------

def _edge_key(edge: dict) -> str:
    """Stable string key for deduplicating / comparing edges."""
    return f"{edge['from']}|{edge['to']}|{edge['type']}"


def compute_graph_diff(
    previous: dict | None,
    current: dict | None,
) -> dict:
    """Compute a structured diff between two graph dicts.

    Returns the stable JSON contract::

        {
            "added_nodes": [{"id": ..., "node": {...}}, ...],
            "removed_nodes": [{"id": ..., "node": {...}}, ...],
            "modified_nodes": [{"id": ..., "before": {...}, "after": {...}}, ...],
            "added_edges": [...],
            "removed_edges": [...],
        }

    If *previous* is ``None``, all current nodes/edges are treated as added.
    If *current* is ``None``, returns an empty diff.
    """
    empty: dict = {
        "added_nodes": [],
        "removed_nodes": [],
        "modified_nodes": [],
        "added_edges": [],
        "removed_edges": [],
    }

    if current is None:
        return empty

    curr_nodes: dict[str, dict] = current.get("nodes", {})
    curr_edges: list[dict] = current.get("edges", [])

    if previous is None:
        return {
            "added_nodes": [
                {"id": nid, "node": node}
                for nid, node in sorted(curr_nodes.items())
            ],
            "removed_nodes": [],
            "modified_nodes": [],
            "added_edges": list(curr_edges),
            "removed_edges": [],
        }

    prev_nodes: dict[str, dict] = previous.get("nodes", {})
    prev_edges: list[dict] = previous.get("edges", [])

    prev_ids = set(prev_nodes.keys())
    curr_ids = set(curr_nodes.keys())

    # Nodes
    added_ids = sorted(curr_ids - prev_ids)
    removed_ids = sorted(prev_ids - curr_ids)
    common_ids = prev_ids & curr_ids

    added_nodes = [{"id": nid, "node": curr_nodes[nid]} for nid in added_ids]
    removed_nodes = [{"id": nid, "node": prev_nodes[nid]} for nid in removed_ids]
    modified_nodes = []

    for nid in sorted(common_ids):
        if prev_nodes[nid] != curr_nodes[nid]:
            modified_nodes.append({
                "id": nid,
                "before": prev_nodes[nid],
                "after": curr_nodes[nid],
            })

    # Edges
    prev_edge_keys = {_edge_key(e): e for e in prev_edges}
    curr_edge_keys = {_edge_key(e): e for e in curr_edges}

    added_edge_set = set(curr_edge_keys.keys()) - set(prev_edge_keys.keys())
    removed_edge_set = set(prev_edge_keys.keys()) - set(curr_edge_keys.keys())

    added_edges = [curr_edge_keys[k] for k in sorted(added_edge_set)]
    removed_edges = [prev_edge_keys[k] for k in sorted(removed_edge_set)]

    return {
        "added_nodes": added_nodes,
        "removed_nodes": removed_nodes,
        "modified_nodes": modified_nodes,
        "added_edges": added_edges,
        "removed_edges": removed_edges,
    }


# ---------------------------------------------------------------------------
# Disk I/O
# ---------------------------------------------------------------------------

def load_and_diff(root: Path) -> dict:
    """Load graphs from disk and compute the diff.

    Reads ``graph.json`` (current) and ``graph-previous.json`` (previous)
    from the ``.weld/`` directory.  If no previous snapshot exists, all
    current nodes are reported as added.  If no current graph exists,
    returns an empty diff.
    """
    weld_dir = root / ".weld"
    current_path = weld_dir / "graph.json"
    previous_path = weld_dir / "graph-previous.json"

    current: dict | None = None
    previous: dict | None = None

    if current_path.is_file():
        try:
            current = json.loads(current_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    if previous_path.is_file():
        try:
            previous = json.loads(previous_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    return compute_graph_diff(previous, current)


# ---------------------------------------------------------------------------
# Human-readable formatting
# ---------------------------------------------------------------------------

def format_human(diff_result: dict) -> str:
    """Format a diff result as human-readable text.

    Example output::

        + 3 nodes added (symbol:py:auth:login, ...)
        ~ 1 node modified (file:routes -- exports changed)
        - 1 node removed (symbol:py:legacy:handler)
        + 5 edges added, - 2 edges removed
    """
    lines: list[str] = []

    added = diff_result["added_nodes"]
    removed = diff_result["removed_nodes"]
    modified = diff_result["modified_nodes"]
    added_edges = diff_result["added_edges"]
    removed_edges = diff_result["removed_edges"]

    if not added and not removed and not modified and not added_edges and not removed_edges:
        return "No changes detected."

    if added:
        count = len(added)
        noun = "node" if count == 1 else "nodes"
        ids = [n["id"] for n in added[:3]]
        suffix = ", ..." if count > 3 else ""
        lines.append(f"+ {count} {noun} added ({', '.join(ids)}{suffix})")

    if modified:
        count = len(modified)
        noun = "node" if count == 1 else "nodes"
        details = []
        for m in modified[:3]:
            nid = m["id"]
            # Summarize what changed
            before_keys = set(_flatten_keys(m["before"]))
            after_keys = set(_flatten_keys(m["after"]))
            changed_fields = []
            for key in sorted(before_keys | after_keys):
                bv = _get_nested(m["before"], key)
                av = _get_nested(m["after"], key)
                if bv != av:
                    changed_fields.append(key.split(".")[-1])
            detail = ", ".join(changed_fields[:2]) + " changed" if changed_fields else "changed"
            details.append(f"{nid} -- {detail}")
        suffix = ", ..." if count > 3 else ""
        lines.append(f"~ {count} {noun} modified ({'; '.join(details)}{suffix})")

    if removed:
        count = len(removed)
        noun = "node" if count == 1 else "nodes"
        ids = [n["id"] for n in removed[:3]]
        suffix = ", ..." if count > 3 else ""
        lines.append(f"- {count} {noun} removed ({', '.join(ids)}{suffix})")

    edge_parts: list[str] = []
    if added_edges:
        count = len(added_edges)
        noun = "edge" if count == 1 else "edges"
        edge_parts.append(f"+ {count} {noun} added")
    if removed_edges:
        count = len(removed_edges)
        noun = "edge" if count == 1 else "edges"
        edge_parts.append(f"- {count} {noun} removed")
    if edge_parts:
        lines.append(", ".join(edge_parts))

    return "\n".join(lines)


def _flatten_keys(d: dict, prefix: str = "") -> list[str]:
    """Flatten a nested dict into dot-separated key paths."""
    keys: list[str] = []
    for k, v in d.items():
        full = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            keys.extend(_flatten_keys(v, full))
        else:
            keys.append(full)
    return keys


def _get_nested(d: dict, key: str):
    """Get a value from a nested dict using a dot-separated key."""
    parts = key.split(".")
    current = d
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """CLI entry point for ``wd diff``."""
    parser = argparse.ArgumentParser(
        prog="wd diff",
        description="Show what changed between the previous and current discovery run.",
    )
    parser.add_argument(
        "root", nargs="?", default=".",
        help="Project root directory (default: .)",
    )
    parser.add_argument(
        "--json", dest="json_output", action="store_true", default=False,
        help="Output machine-readable JSON instead of human summary.",
    )
    args = parser.parse_args(argv)

    from weld._graph_cli import _build_retry_hint, ensure_graph_exists

    # Surface a friendly first-run message when the graph has not been
    # built yet; mirrors the behaviour of read commands in _graph_cli
    # (tracked issue / tracked issue).
    ensure_graph_exists(Path(args.root), _build_retry_hint("diff"))

    result = load_and_diff(Path(args.root))

    if args.json_output:
        json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        print(format_human(result))

    return 0
