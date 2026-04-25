"""Static change planning for persisted Agent Graphs."""

from __future__ import annotations

import re
from typing import Any

from weld.agent_graph_inventory import asset_entries, impact_asset

_STOP_WORDS = frozenset({
    "a",
    "about",
    "add",
    "always",
    "and",
    "be",
    "change",
    "changes",
    "for",
    "include",
    "make",
    "must",
    "of",
    "please",
    "should",
    "the",
    "to",
    "update",
    "with",
})


def plan_change(graph: dict[str, Any], request: str) -> dict[str, Any]:
    """Return a deterministic static change plan for *request*."""
    assets = asset_entries(graph)
    scored_assets = _rank_assets(assets, request)
    primary_assets = _primary_assets(scored_assets)
    impacts = [
        impact
        for asset in primary_assets
        if (impact := impact_asset(graph, asset["id"])) is not None
    ]
    secondary_assets = _secondary_assets(impacts, primary_assets)
    primary_files = _paths(primary_assets)
    secondary_files = _paths(secondary_assets)
    return {
        "primary_assets": primary_assets,
        "primary_files": primary_files,
        "request": request,
        "secondary_assets": secondary_assets,
        "secondary_files": secondary_files,
        "validation_files": _validation_files(primary_files, secondary_files),
        "validation_steps": _validation_steps(primary_assets),
        "warnings": _warnings(primary_assets, impacts),
    }


def _rank_assets(
    assets: list[dict[str, Any]],
    request: str,
) -> list[dict[str, Any]]:
    terms = _request_terms(request)
    ranked: list[dict[str, Any]] = []
    for asset in assets:
        score, matched_terms = _score_asset(asset, terms)
        if score <= 0:
            continue
        copy = dict(asset)
        copy["match_score"] = score
        copy["matched_terms"] = matched_terms
        ranked.append(copy)
    return sorted(
        ranked,
        key=lambda item: (
            -item["match_score"],
            item["platform_name"].casefold(),
            item["type"],
            item["name"].casefold(),
            item["path"],
            item["id"],
        ),
    )


def _primary_assets(scored_assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not scored_assets:
        return []
    best_score = scored_assets[0]["match_score"]
    return [asset for asset in scored_assets if asset["match_score"] == best_score][:5]


def _secondary_assets(
    impacts: list[dict[str, Any]],
    primary_assets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    primary_ids = {asset["id"] for asset in primary_assets}
    secondary: dict[str, dict[str, Any]] = {}
    for impact in impacts:
        for key in ("affected_nodes", "same_name_variants", "same_purpose_variants"):
            for asset in impact.get(key, []):
                if asset["id"] not in primary_ids:
                    secondary[asset["id"]] = asset
    return _sort_assets(list(secondary.values()))


def _validation_files(
    primary_files: list[str],
    secondary_files: list[str],
) -> list[str]:
    return sorted({".weld/agent-graph.json", *primary_files, *secondary_files})


def _validation_steps(primary_assets: list[dict[str, Any]]) -> list[str]:
    steps = ["wd agents audit"]
    for asset in primary_assets:
        steps.append(f"wd agents explain {asset['id']}")
        steps.append(f"wd agents impact {asset['id']}")
    if not primary_assets:
        steps.append("wd agents list")
    return steps


def _warnings(
    primary_assets: list[dict[str, Any]],
    impacts: list[dict[str, Any]],
) -> list[str]:
    if not primary_assets:
        return ["No matching assets found; authoritative source is unknown."]
    warnings: list[str] = []
    for asset in primary_assets:
        if asset["status"] == "manual":
            warnings.append(f"Authoritative source is unknown for {asset['name']}.")
        if not asset["path"]:
            warnings.append(f"Primary asset {asset['id']} has no source file path.")
    for impact in impacts:
        asset = impact["asset"]
        if impact.get("same_name_variants"):
            warnings.append(f"Platform variants may drift for {asset['name']}.")
        if impact.get("same_purpose_variants"):
            warnings.append(f"Same-purpose variants may need updates for {asset['name']}.")
    return sorted(set(warnings))


def _score_asset(
    asset: dict[str, Any],
    terms: list[str],
) -> tuple[int, list[str]]:
    score = 0
    matched: set[str] = set()
    name_terms = set(_words(asset["name"]))
    description_terms = set(_words(asset["description"]))
    path_terms = set(_words(asset["path"]))
    type_terms = set(_words(asset["type"]))
    for term in terms:
        if term in name_terms:
            score += 5
            matched.add(term)
        if term in description_terms:
            score += 2
            matched.add(term)
        if term in path_terms:
            score += 1
            matched.add(term)
        if term in type_terms:
            score += 1
            matched.add(term)
    return score, sorted(matched)


def _request_terms(request: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for term in _words(request):
        if term in _STOP_WORDS or term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return terms


def _paths(assets: list[dict[str, Any]]) -> list[str]:
    return sorted({asset["path"] for asset in assets if asset["path"]})


def _sort_assets(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        assets,
        key=lambda item: (
            item["platform_name"].casefold(),
            item["type"],
            item["name"].casefold(),
            item["path"],
            item["id"],
        ),
    )


def _words(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", value.casefold())
