"""Custom edge rules for ``weld.arch_lint``."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from weld._yaml import parse_yaml

CUSTOM_RULES_FILENAME = "lint-rules.yaml"
_SELECTOR_KEYS = frozenset({"type", "path_match", "id_match", "label_match"})


@dataclass(frozen=True)
class CustomViolation:
    rule: str
    node_id: str
    message: str
    severity: str


@dataclass(frozen=True)
class CustomRule:
    rule_id: str
    description: str
    deny: list[dict]
    allow: list[dict]
    severity: str
    message: str | None

    def check(self, data: dict) -> Iterable[CustomViolation]:
        nodes = data.get("nodes", {}) or {}
        edges = sorted(data.get("edges", []) or [], key=_edge_sort_key)
        for edge in edges:
            if not any(_edge_matches(edge, nodes, matcher) for matcher in self.deny):
                continue
            if any(_edge_matches(edge, nodes, matcher) for matcher in self.allow):
                continue
            from_id = str(edge.get("from", ""))
            to_id = str(edge.get("to", ""))
            yield CustomViolation(
                rule=self.rule_id,
                node_id=from_id,
                message=self.message
                or f"edge {from_id!r} -> {to_id!r} violates custom lint rule {self.rule_id!r}",
                severity=self.severity,
            )


def load_custom_rules(
    path: Path,
    known_ids: Iterable[str],
) -> tuple[list[CustomRule], list[str]]:
    """Load custom edge-deny rules from ``.weld/lint-rules.yaml``."""
    if not path.exists():
        return [], []
    warnings: list[str] = []
    try:
        data = parse_yaml(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - parser raises are rare.
        return [], [f"{path}: failed to parse custom lint rules: {exc}"]
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        return [], [f"{path}: expected top-level 'rules' list"]

    rules: list[CustomRule] = []
    seen = set(known_ids)
    for index, entry in enumerate(data["rules"]):
        context = f"{path}: rules[{index}]"
        rule = _custom_rule_from_entry(entry, context, seen, warnings)
        if rule is not None:
            seen.add(rule.rule_id)
            rules.append(rule)
    return rules, warnings


def _custom_rule_from_entry(
    entry: object,
    context: str,
    seen: set[str],
    warnings: list[str],
) -> CustomRule | None:
    if not isinstance(entry, dict):
        warnings.append(f"{context}: expected mapping")
        return None
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        warnings.append(f"{context}: expected non-empty string 'name'")
        return None
    rule_id = name.strip()
    if rule_id in seen:
        warnings.append(f"{context}: duplicate rule id {rule_id!r}")
        return None

    deny = _edge_matchers(entry.get("deny"), f"{context}.deny", warnings)
    allow = _edge_matchers(entry.get("allow"), f"{context}.allow", warnings)
    if not deny:
        warnings.append(f"{context}: expected at least one deny matcher")
        return None
    severity = entry.get("severity", "error")
    if severity not in {"error", "warning"}:
        warnings.append(f"{context}: severity must be 'error' or 'warning'")
        return None
    message = entry.get("message")
    if message is not None and not isinstance(message, str):
        warnings.append(f"{context}: message must be a string")
        return None
    description = entry.get("description")
    if not isinstance(description, str) or not description.strip():
        description = f"Custom deny rule loaded from {CUSTOM_RULES_FILENAME}."
    return CustomRule(rule_id, description, deny, allow, severity, message)


def _edge_matchers(value: object, context: str, warnings: list[str]) -> list[dict]:
    if value is None:
        return []
    entries = value if isinstance(value, list) else [value]
    matchers: list[dict] = []
    for index, entry in enumerate(entries):
        item_context = f"{context}[{index}]"
        if not isinstance(entry, dict):
            warnings.append(f"{item_context}: expected mapping")
            continue
        matcher = _edge_matcher(entry, item_context, warnings)
        if matcher is not None:
            matchers.append(matcher)
    return matchers


def _edge_matcher(entry: dict, context: str, warnings: list[str]) -> dict | None:
    from_selector = _selector(entry.get("from"), f"{context}.from", warnings)
    to_selector = _selector(entry.get("to"), f"{context}.to", warnings)
    edge_type = entry.get("edge_type")
    if edge_type is not None and not isinstance(edge_type, str):
        warnings.append(f"{context}.edge_type: expected string")
        return None
    unknown = sorted(set(entry) - {"from", "to", "edge_type"})
    if unknown:
        warnings.append(f"{context}: unknown matcher keys: {', '.join(unknown)}")
        return None
    if from_selector is None or to_selector is None:
        return None
    return {"from": from_selector, "to": to_selector, "edge_type": edge_type}


def _selector(value: object, context: str, warnings: list[str]) -> dict | None:
    if value is None:
        return {}
    if not isinstance(value, dict):
        warnings.append(f"{context}: expected mapping")
        return None
    unknown = sorted(set(value) - _SELECTOR_KEYS)
    if unknown:
        warnings.append(f"{context}: unknown selector keys: {', '.join(unknown)}")
        return None
    selector: dict[str, str] = {}
    for key, raw in value.items():
        if not isinstance(raw, str):
            warnings.append(f"{context}.{key}: expected string")
            return None
        selector[key] = raw
    return selector


def _edge_sort_key(edge: dict) -> tuple[str, str, str]:
    return (
        str(edge.get("from", "")),
        str(edge.get("to", "")),
        str(edge.get("type", "")),
    )


def _edge_matches(edge: dict, nodes: dict, matcher: dict) -> bool:
    edge_type = matcher.get("edge_type")
    if edge_type is not None and edge.get("type") != edge_type:
        return False
    from_id = edge.get("from")
    to_id = edge.get("to")
    if not isinstance(from_id, str) or not isinstance(to_id, str):
        return False
    return _node_matches(from_id, nodes.get(from_id), matcher["from"]) and _node_matches(
        to_id, nodes.get(to_id), matcher["to"]
    )


def _node_matches(node_id: str, node: dict | None, selector: dict) -> bool:
    if node is None:
        return False
    if selector.get("type") and node.get("type") != selector["type"]:
        return False
    if selector.get("id_match") and not fnmatch.fnmatchcase(node_id, selector["id_match"]):
        return False
    label = str(node.get("label", ""))
    if selector.get("label_match") and not fnmatch.fnmatchcase(
        label, selector["label_match"]
    ):
        return False
    pattern = selector.get("path_match")
    if pattern is None:
        return True
    path = _node_path(node)
    return path is not None and fnmatch.fnmatchcase(path, pattern)


def _node_path(node: dict) -> str | None:
    props = node.get("props") or {}
    for key in ("file", "path"):
        value = props.get(key)
        if isinstance(value, str):
            return value.replace("\\", "/").lstrip("./")
    discovered = props.get("discovered_from")
    if isinstance(discovered, str):
        return discovered.replace("\\", "/").lstrip("./")
    if isinstance(discovered, list):
        for value in discovered:
            if isinstance(value, str):
                return value.replace("\\", "/").lstrip("./")
    return None
