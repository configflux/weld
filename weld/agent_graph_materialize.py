"""Materialize parsed Agent Graph metadata into nodes and edges.

ADR 0041 (PR 2) wires this module through the canonical ID contract
(``weld._node_ids``) and the unified ``ensure_node`` merge primitive
(``weld._graph_node_registry``). Two paths that reach the same logical
entity (a Phase 1 SKILL.md asset and a Phase 3 ``uses_skills`` reference;
two SKILL.md files classified as the same generic skill) merge into one
canonical node rather than splitting via a SHA1-suffixed disambiguator.
The historical ``_node_id_for_values`` helper is removed by construction.

ADR 0041 § Migration -- the pre-rename SHA1-suffix form is recorded on
each asset's ``aliases`` list via :func:`legacy_skill_id_with_suffix` so
external consumers (MCP transcripts, sidecar caches, prior ``wd query``
outputs) that reference the legacy ID resolve transparently for one
minor version. The alias-aware lookup index lives in
:mod:`weld.graph_query` / :mod:`weld.graph_context`.
"""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any

from weld._graph_node_registry import ensure_node
from weld._node_ids import canonical_slug, entity_id
from weld.agent_graph_metadata import AgentGraphReference, ParsedAgentGraphAsset


def legacy_skill_id_with_suffix(
    node_type: str, platform: str, name: str, path: str,
) -> str:
    """Reproduce the pre-ADR-0041 SHA1-suffixed ID for *path*.

    The legacy ``agent_graph_materialize._node_id_for_values`` minted
    ``<base>:<sha1(path)[:8]>`` whenever two assets collided on
    ``<base>``. ADR 0041 retires the suffix path; the canonical merge
    primitive merges instead. This helper reproduces the historical
    suffix form so it can be recorded on the merged node's
    ``aliases`` list and the alias-aware lookup keeps working for the
    deprecation window (one minor version per ADR 0041).

    The base form is intentionally identical to :func:`entity_id`'s
    output (``canonical_slug`` applied to ``type:platform:name``); the
    suffix is the first eight hex chars of the SHA1 of the
    UTF-8-encoded source path. Both halves are deterministic across
    operating systems.
    """
    base = entity_id(node_type, platform=platform, name=name)
    suffix = hashlib.sha1(path.encode("utf-8")).hexdigest()[:8]
    return f"{base}:{suffix}"


def materialize_agent_graph(
    root: Path,
    assets: list[Any],
    parsed: dict[str, ParsedAgentGraphAsset],
    *,
    platform_labels: dict[str, str],
    source_strategy: str,
) -> tuple[dict[str, dict], list[dict], dict[str, str]]:
    """Build deterministic graph nodes and edges from parsed static assets."""
    nodes, source_ids, index = _nodes_for_assets(
        assets, parsed, platform_labels, source_strategy,
    )
    edges = _edges_for_assets(
        root, assets, parsed, nodes, source_ids, index, platform_labels, source_strategy,
    )
    return nodes, edges, source_ids


def diagnostics_for_assets(
    assets: list[Any],
    parsed: dict[str, ParsedAgentGraphAsset],
    source_ids: dict[str, str],
) -> list[dict]:
    """Attach source node provenance to parser diagnostics."""
    diagnostics: list[dict] = []
    for asset in assets:
        for diagnostic in parsed[asset.path].diagnostics:
            copy_diag = copy.deepcopy(diagnostic)
            copy_diag.setdefault("source_node", source_ids[asset.path])
            diagnostics.append(copy_diag)
    return diagnostics


def _nodes_for_assets(
    assets: list[Any],
    parsed: dict[str, ParsedAgentGraphAsset],
    platform_labels: dict[str, str],
    source_strategy: str,
) -> tuple[dict[str, dict], dict[str, str], dict[tuple[str, str], list[str]]]:
    nodes: dict[str, dict] = {}
    source_ids: dict[str, str] = {}
    index: dict[tuple[str, str], list[str]] = {}
    for asset in assets:
        node_id = entity_id(
            asset.node_type, platform=asset.platform, name=asset.name,
        )
        props = asset.props()
        props.update(copy.deepcopy(parsed[asset.path].props))
        ensure_node(
            nodes,
            node_id,
            asset.node_type,
            source_strategy=source_strategy,
            source_path=asset.path,
            authority="external",
            props=props,
            legacy_id=legacy_skill_id_with_suffix(
                asset.node_type, asset.platform, asset.name, asset.path,
            ),
        )
        source_ids[asset.path] = node_id
        _index_node(index, nodes, node_id)
    for asset in assets:
        for derived in parsed[asset.path].derived_nodes:
            props = {
                "file": derived.path,
                "name": derived.name,
                "platform": derived.platform,
                "platform_name": platform_labels.get(derived.platform, derived.platform),
                "source_kind": derived.source_kind,
                "source_platform": derived.platform,
                "source_strategy": source_strategy,
                "status": "manual",
            }
            props.update(copy.deepcopy(derived.props))
            node_id = entity_id(
                derived.node_type, platform=derived.platform, name=derived.name,
            )
            ensure_node(
                nodes,
                node_id,
                derived.node_type,
                source_strategy=source_strategy,
                source_path=derived.path,
                authority="external",
                props=props,
                legacy_id=legacy_skill_id_with_suffix(
                    derived.node_type, derived.platform, derived.name, derived.path,
                ),
            )
            source_ids[derived.path] = node_id
            _index_node(index, nodes, node_id)
    return nodes, source_ids, index


def _edges_for_assets(
    root: Path,
    assets: list[Any],
    parsed: dict[str, ParsedAgentGraphAsset],
    nodes: dict[str, dict],
    source_ids: dict[str, str],
    index: dict[tuple[str, str], list[str]],
    platform_labels: dict[str, str],
    source_strategy: str,
) -> list[dict]:
    edges: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    for asset in assets:
        source_id = source_ids[asset.path]
        _add_reference_edges(
            root, asset.path, asset.platform, source_id, parsed[asset.path].references,
            nodes, index, edges, seen, platform_labels, source_strategy,
        )
        for derived in parsed[asset.path].derived_nodes:
            derived_id = source_ids[derived.path]
            edge_type = "triggers_on_event" if derived.node_type == "hook" else "configures"
            _add_edge(edges, seen, source_id, derived_id, edge_type, {
                "source_strategy": source_strategy,
                "confidence": "definite",
                "provenance": {"file": asset.path, "line": 1, "raw": derived.path},
            })
            _add_reference_edges(
                root, asset.path, derived.platform, derived_id, derived.references,
                nodes, index, edges, seen, platform_labels, source_strategy,
            )
    return edges


def _add_reference_edges(
    root: Path,
    source_path: str,
    source_platform: str,
    source_id: str,
    references: tuple[AgentGraphReference, ...],
    nodes: dict[str, dict],
    index: dict[tuple[str, str], list[str]],
    edges: list[dict],
    seen: set[tuple[str, str, str, str]],
    platform_labels: dict[str, str],
    source_strategy: str,
) -> None:
    for reference in references:
        for target_id in _target_ids(
            root, source_path, source_platform, reference, nodes,
            index, platform_labels, source_strategy,
        ):
            _add_edge(edges, seen, source_id, target_id, reference.edge_type, {
                "source_strategy": source_strategy,
                "confidence": reference.confidence,
                "provenance": {
                    "file": source_path,
                    "line": reference.line,
                    "raw": reference.raw,
                    "target": reference.target_name,
                    "target_type": reference.target_type,
                },
            })


def _target_ids(
    root: Path,
    source_path: str,
    source_platform: str,
    reference: AgentGraphReference,
    nodes: dict[str, dict],
    index: dict[tuple[str, str], list[str]],
    platform_labels: dict[str, str],
    source_strategy: str,
) -> list[str]:
    if reference.target_type == "file":
        target = _resolved_file(root, source_path, reference.target_path or reference.target_name)
        return [_ensure_referenced_node(
            nodes, index, "file", "generic", target or reference.target_name,
            {"file": target or reference.target_name, "exists": target is not None},
            platform_labels, source_strategy, source_path,
        )]
    if reference.target_type in {"scope", "tool"}:
        return [_ensure_referenced_node(
            nodes, index, reference.target_type, "generic",
            reference.target_name, {}, platform_labels, source_strategy, source_path,
        )]

    matches = index.get((reference.target_type, canonical_slug(reference.target_name)), [])
    same_platform = [
        node_id for node_id in matches
        if nodes[node_id]["props"].get("platform") == source_platform
    ]
    if same_platform or matches:
        return same_platform or matches[:1]
    return [_ensure_referenced_node(
        nodes, index, reference.target_type, source_platform,
        reference.target_name, {"status": "referenced"},
        platform_labels, source_strategy, source_path,
    )]


def _ensure_referenced_node(
    nodes: dict[str, dict],
    index: dict[tuple[str, str], list[str]],
    node_type: str,
    platform: str,
    name: str,
    props: dict[str, Any],
    platform_labels: dict[str, str],
    source_strategy: str,
    source_path: str,
) -> str:
    node_id = entity_id(node_type, platform=platform, name=name)
    existing = index.get((node_type, canonical_slug(name)), [])
    if existing and node_type in {"file", "scope", "tool"}:
        return existing[0]
    merged = {"name": name, "source_strategy": source_strategy, "status": "referenced"}
    if platform != "generic":
        merged["platform"] = platform
        merged["platform_name"] = platform_labels.get(platform, platform)
    merged.update(copy.deepcopy(props))
    # Reference-time legacy form: the historical ``_node_id_for_values``
    # used the *target* path (``str(merged.get("file", name))``) for the
    # SHA1 suffix on collision. Reference fall-throughs that materialise
    # a sentinel record the corresponding alias so external transcripts
    # that pasted the suffixed form still resolve through the
    # alias-aware lookup.
    legacy_path = str(merged.get("file") or name)
    legacy_id = legacy_skill_id_with_suffix(node_type, platform, name, legacy_path)
    ensure_node(
        nodes,
        node_id,
        node_type,
        source_strategy=source_strategy,
        source_path=source_path,
        authority="referenced",
        props=merged,
        legacy_id=legacy_id,
    )
    _index_node(index, nodes, node_id)
    return node_id


def _resolved_file(root: Path, source_path: str, target: str) -> str | None:
    for candidate in (root / target, (root / source_path).parent / target):
        try:
            rel = candidate.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            continue
        if (root / rel).is_file():
            return rel
    return None


def _index_node(index: dict[tuple[str, str], list[str]], nodes: dict[str, dict], node_id: str) -> None:
    node = nodes[node_id]
    names = {node["label"], node.get("props", {}).get("name")}
    for name in names:
        if not name:
            continue
        key = (node["type"], canonical_slug(str(name)))
        bucket = index.setdefault(key, [])
        if node_id not in bucket:
            bucket.append(node_id)


def _add_edge(
    edges: list[dict],
    seen: set[tuple[str, str, str, str]],
    from_id: str,
    to_id: str,
    edge_type: str,
    props: dict[str, Any],
) -> None:
    raw = str(props.get("provenance", {}).get("raw", ""))
    key = (from_id, to_id, edge_type, raw)
    if key in seen:
        return
    seen.add(key)
    edges.append({"from": from_id, "to": to_id, "type": edge_type, "props": props})
