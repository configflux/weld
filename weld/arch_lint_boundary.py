"""Boundary-enforcement rule for ``weld.arch_lint``.

Detects edges that cross declared layer boundaries without an explicit
topology declaration in ``discover.yaml``.  Nodes declare their layer via
``props.layer``; allowed crossings are declared under
``topology.allowed_cross_layer``.

Example ``discover.yaml`` snippet::

    topology:
      allowed_cross_layer:
        - from: api
          to: domain
        - from: domain
          to: infra
          edge_type: imports     # optional: restrict by edge type
        - from: "*"
          to: shared             # wildcard matches any layer

Edges between nodes that lack a ``layer`` property are silently skipped.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class _Violation:
    """Local mirror of ``arch_lint.Violation`` to avoid circular import."""

    rule: str
    node_id: str
    message: str
    severity: str = "error"

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "node_id": self.node_id,
            "message": self.message,
            "severity": self.severity,
        }


def _load_allowed_crossings(root: Path) -> list[dict]:
    """Read ``topology.allowed_cross_layer`` from discover.yaml."""
    config_path = root / ".weld" / "discover.yaml"
    if not config_path.is_file():
        return []

    from weld._yaml import parse_yaml

    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return []
    config = parse_yaml(text)
    if not isinstance(config, dict):
        return []
    topology = config.get("topology", {})
    if not isinstance(topology, dict):
        return []
    entries = topology.get("allowed_cross_layer", [])
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, dict)]


def _crossing_allowed(
    from_layer: str,
    to_layer: str,
    edge_type: str,
    allowed: list[dict],
) -> bool:
    """Return True if the crossing is declared in allowed_cross_layer."""
    for entry in allowed:
        a_from = str(entry.get("from", ""))
        a_to = str(entry.get("to", ""))
        a_type = entry.get("edge_type")

        from_ok = a_from == "*" or a_from == from_layer
        to_ok = a_to == "*" or a_to == to_layer
        type_ok = a_type is None or str(a_type) == edge_type

        if from_ok and to_ok and type_ok:
            return True
    return False


def rule_boundary_enforcement(
    data: dict,
    root: Path,
) -> Iterable[_Violation]:
    """Flag edges crossing layer boundaries without a topology declaration.

    Scans every edge in the graph.  When both the source and target node
    declare a ``layer`` property and those layers differ, the edge is
    checked against ``topology.allowed_cross_layer``.  Undeclared crossings
    yield a warning-severity violation.
    """
    nodes: dict = data.get("nodes", {}) or {}
    edges: list = data.get("edges", []) or []
    allowed = _load_allowed_crossings(root)

    violations: list[tuple[str, str]] = []  # (from_id, message)

    for edge in edges:
        from_id = edge.get("from")
        to_id = edge.get("to")
        if not isinstance(from_id, str) or not isinstance(to_id, str):
            continue

        from_node = nodes.get(from_id)
        to_node = nodes.get(to_id)
        if from_node is None or to_node is None:
            continue

        from_props = from_node.get("props") or {}
        to_props = to_node.get("props") or {}
        from_layer = from_props.get("layer")
        to_layer = to_props.get("layer")

        if not isinstance(from_layer, str) or not isinstance(to_layer, str):
            continue
        if from_layer == to_layer:
            continue

        edge_type = str(edge.get("type", ""))
        if _crossing_allowed(from_layer, to_layer, edge_type, allowed):
            continue

        msg = (
            f"edge {from_id!r} -> {to_id!r} ({edge_type}) crosses "
            f"layer boundary ({from_layer} -> {to_layer}) without a "
            f"topology declaration in discover.yaml"
        )
        violations.append((from_id, msg))

    for from_id, msg in sorted(violations, key=lambda t: t[0]):
        yield _Violation(
            rule="boundary-enforcement",
            node_id=from_id,
            message=msg,
            severity="warning",
        )
