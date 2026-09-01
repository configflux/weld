"""``wd doctor``'s Graph/Schema/Nodes/Edges section (ADR 0137 ss3).

Split out of :mod:`weld.doctor`, which sits at the 400-line cap, on the same
rule as its sibling ``_doctor_*`` check modules: the module there is the
section registry and the formatter, and each check's body lives next to the
thing it inspects.

The Edges line gained a second question with ADR 0137. Reporting an edge
*count* says nothing about whether those edges point anywhere: the v0.24.0
field evaluation found a federated root whose every cross-repo edge referenced
a node that existed in neither the root nor any child, and doctor called it
healthy because it had only ever counted. At a workspace root the endpoints
are now classified against the ids the workspace actually holds.

That makes this the first doctor check to read *child* graphs, so at a
workspace root the command's cost now scales with the workspace rather than
the root alone. It reads node ids only, and only at a root that registers
children; a single repo pays nothing. The alternative -- reporting healthy
because looking would have cost something -- is the failure this check exists
to end.
"""

from __future__ import annotations

import json
from pathlib import Path

from weld._federation_validate import (
    DANGLING_REF_MESSAGE_PREFIX,
    UNVERIFIABLE_REF_MESSAGE_PREFIX,
)

__all__ = ["check_graph_json"]


def check_graph_json(weld_dir: Path, result_cls) -> list:
    """Report graph.json presence + schema/nodes/edges split into sections."""
    path = weld_dir / "graph.json"
    if not path.is_file():
        return [result_cls("fail", ".weld/graph.json not found", "Graph")]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return [
            result_cls("fail", ".weld/graph.json is invalid or unreadable", "Graph")
        ]

    nodes = data.get("nodes", {})
    edges = data.get("edges", [])
    meta = data.get("meta", {}) or {}
    schema_ver = meta.get("schema_version", "?")
    n_nodes = len(nodes) if isinstance(nodes, dict) else 0
    n_edges = len(edges) if isinstance(edges, list) else 0
    return [
        result_cls(
            "ok",
            f".weld/graph.json found ({n_nodes} nodes, {n_edges} edges, schema v{schema_ver})",
            "Graph",
        ),
        result_cls("ok", f"schema v{schema_ver}", "Schema"),
        result_cls("ok", f"{n_nodes} nodes", "Nodes"),
        result_cls("ok", f"{n_edges} edges", "Edges"),
    ] + check_cross_repo_endpoints(weld_dir.parent, data, result_cls)


def check_cross_repo_endpoints(root: Path, data: dict, result_cls) -> list:
    """Report edge endpoints a federated *root* cannot resolve.

    Returns nothing outside a workspace root (there is no child id space to
    resolve into) and nothing when every endpoint resolves. Doctor must never
    raise, so a failure to build the index is reported as a warning rather
    than swallowed -- an unrun check that looks like a passed one is the
    reporting failure this whole section exists to stop.
    """
    from weld._federation_ids import federation_id_index_for_root
    from weld.contract import validate_graph

    try:
        index = federation_id_index_for_root(root)
        if index is None:
            return []
        errors = validate_graph(data, id_index=index)
    except Exception as exc:  # noqa: BLE001 -- doctor reports, never raises
        return [result_cls(
            "warn",
            "cross-repo edge endpoints could not be checked "
            f"({type(exc).__name__}); run `wd workspace status`",
            "Edges",
        )]

    dangling = sum(
        1 for err in errors
        if err.message.startswith(DANGLING_REF_MESSAGE_PREFIX)
    )
    unverifiable = sum(
        1 for err in errors
        if err.message.startswith(UNVERIFIABLE_REF_MESSAGE_PREFIX)
    )
    if not dangling and not unverifiable:
        return []

    parts = []
    if dangling:
        parts.append(f"{dangling} dangling")
    if unverifiable:
        parts.append(f"{unverifiable} unverifiable")
    plural = "" if dangling + unverifiable == 1 else "s"
    return [result_cls(
        "fail",
        f"{' and '.join(parts)} cross-repo edge endpoint{plural}; "
        "run `wd graph validate` to list them",
        "Edges",
    )]
