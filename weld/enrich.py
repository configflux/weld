"""Built-in semantic enrichment orchestration."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from weld.graph import Graph
from weld.providers import EnrichmentProvider, resolve_provider

ENRICH_VERSION = 1
_COMPLEXITY_HINTS = frozenset(["low", "medium", "high"])
_FINGERPRINT_EXCLUDED_PROPS = frozenset(["description", "purpose", "enrichment"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_non_negative_int(raw: str) -> int:
    value = int(raw)
    if value < 0:
        raise argparse.ArgumentTypeError("value must be >= 0")
    return value


def _parse_non_negative_float(raw: str) -> float:
    value = float(raw)
    if value < 0:
        raise argparse.ArgumentTypeError("value must be >= 0")
    return value


def _resolve_provider_name(provider_name: str | None) -> str:
    resolved = (provider_name or os.getenv("WELD_ENRICH_PROVIDER", "")).strip().lower()
    if not resolved:
        raise ValueError(
            "provider is required (use --provider or set WELD_ENRICH_PROVIDER)"
        )
    return resolved


def _selected_node_ids(graph: Graph, node_id: str | None) -> list[str]:
    if node_id is not None:
        if graph.get_node(node_id) is None:
            raise ValueError(f"node not found: {node_id}")
        return [node_id]
    nodes = graph.dump().get("nodes", {})
    return sorted(nodes, key=lambda nid: (nodes[nid].get("type", ""), nid))


def _snapshot(graph: Graph) -> dict:
    return json.loads(json.dumps(graph.dump()))


def _snapshot_node(snapshot: dict, node_id: str) -> dict | None:
    node = snapshot.get("nodes", {}).get(node_id)
    if node is None:
        return None
    return {"id": node_id, **node}


def _snapshot_neighbors(snapshot: dict, node_id: str) -> list[dict]:
    neighbor_ids: set[str] = set()
    for edge in snapshot.get("edges", []):
        if edge.get("from") == node_id:
            neighbor_ids.add(edge["to"])
        elif edge.get("to") == node_id:
            neighbor_ids.add(edge["from"])
    neighbors: list[dict] = []
    for neighbor_id in sorted(neighbor_ids):
        node = snapshot.get("nodes", {}).get(neighbor_id)
        if node is not None:
            neighbors.append({"id": neighbor_id, **node})
    return neighbors


def _enrichment_fingerprint(node: dict, neighbors: list[dict]) -> str:
    def stable_props(entry: dict) -> dict:
        props = entry.get("props") or {}
        return {
            key: value
            for key, value in props.items()
            if key not in _FINGERPRINT_EXCLUDED_PROPS
        }

    payload = {
        "node": {
            "id": node.get("id"),
            "type": node.get("type"),
            "label": node.get("label"),
            "props": stable_props(node),
        },
        "neighbors": [
            {
                "id": neighbor.get("id"),
                "type": neighbor.get("type"),
                "label": neighbor.get("label"),
                "props": stable_props(neighbor),
            }
            for neighbor in neighbors
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _valid_cached_enrichment(
    node: dict,
    *,
    provider_name: str,
    model: str,
    fingerprint: str,
) -> bool:
    props = node.get("props") or {}
    enrichment = props.get("enrichment")
    if not isinstance(enrichment, dict):
        return False
    required = ("provider", "model", "timestamp", "description")
    for key in required:
        value = enrichment.get(key)
        if not isinstance(value, str) or not value.strip():
            return False
    return (
        enrichment["provider"].strip().lower() == provider_name
        and enrichment["model"].strip() == model
        and enrichment.get("fingerprint") == fingerprint
    )


def _normalize_tags(raw_tags: tuple[str, ...]) -> list[str]:
    tags: list[str] = []
    for tag in raw_tags:
        cleaned = tag.strip().lower()
        if cleaned and cleaned not in tags:
            tags.append(cleaned)
    return tags


def _stored_enrichment(
    *,
    provider_name: str,
    model: str,
    fingerprint: str,
    description: str,
    purpose: str | None,
    complexity_hint: str | None,
    suggested_tags: tuple[str, ...],
) -> dict:
    stored = {
        "provider": provider_name,
        "model": model,
        "timestamp": _now(),
        "fingerprint": fingerprint,
        "description": description.strip(),
    }
    if purpose:
        stored["purpose"] = purpose.strip()
    if complexity_hint:
        normalized = complexity_hint.strip().lower()
        if normalized not in _COMPLEXITY_HINTS:
            raise ValueError("complexity_hint must be one of low, medium, high")
        stored["complexity_hint"] = normalized
    tags = _normalize_tags(suggested_tags)
    if tags:
        stored["suggested_tags"] = tags
    return stored


def _budget_reached(
    *,
    tokens_used: int,
    cost_used: float,
    max_tokens: int | None,
    max_cost: float | None,
) -> bool:
    if max_tokens is not None and tokens_used >= max_tokens:
        return True
    if max_cost is not None and cost_used >= max_cost:
        return True
    return False


def _usage_block(tokens_used: int, cost_used: float) -> dict:
    return {
        "tokens_used": tokens_used,
        "cost_usd": round(cost_used, 6),
    }


def run_enrichment(
    graph: Graph,
    *,
    provider: EnrichmentProvider,
    provider_name: str,
    model: str | None = None,
    node_id: str | None = None,
    force: bool = False,
    max_tokens: int | None = None,
    max_cost: float | None = None,
    persist: bool = True,
) -> dict:
    """Run enrichment using an already-instantiated *provider*."""

    if max_tokens is not None and max_tokens < 0:
        raise ValueError("max_tokens must be >= 0")
    if max_cost is not None and max_cost < 0:
        raise ValueError("max_cost must be >= 0")

    selected_ids = _selected_node_ids(graph, node_id)
    baseline = _snapshot(graph)
    resolved_model = model or provider.DEFAULT_MODEL
    result = {
        "enrich_version": ENRICH_VERSION,
        "provider": provider_name,
        "model": resolved_model,
        "requested": len(selected_ids),
        "enriched": [],
        "skipped": [],
        "errors": [],
        "partial": False,
        "usage": _usage_block(0, 0.0),
    }
    tokens_used = 0
    cost_used = 0.0
    changed = False

    for index, current_id in enumerate(selected_ids):
        if _budget_reached(
            tokens_used=tokens_used,
            cost_used=cost_used,
            max_tokens=max_tokens,
            max_cost=max_cost,
        ):
            for pending_id in selected_ids[index:]:
                result["errors"].append({"node_id": pending_id, "error": "budget exceeded"})
            result["partial"] = True
            break

        baseline_node = _snapshot_node(baseline, current_id)
        live_node = graph.get_node(current_id)
        if baseline_node is None or live_node is None:
            result["errors"].append({"node_id": current_id, "error": "node not found"})
            result["partial"] = True
            continue
        neighbors = _snapshot_neighbors(baseline, current_id)
        fingerprint = _enrichment_fingerprint(baseline_node, neighbors)

        if not force and _valid_cached_enrichment(
            live_node,
            provider_name=provider_name,
            model=resolved_model,
            fingerprint=fingerprint,
        ):
            result["skipped"].append(current_id)
            continue

        try:
            enrichment = provider.enrich(
                baseline_node,
                neighbors,
                model=resolved_model,
            )
            stored = _stored_enrichment(
                provider_name=provider_name,
                model=resolved_model,
                fingerprint=fingerprint,
                description=enrichment.description,
                purpose=enrichment.purpose,
                complexity_hint=enrichment.complexity_hint,
                suggested_tags=enrichment.suggested_tags,
            )
        except Exception as exc:  # pragma: no cover - exercised by black-box QA
            result["errors"].append({"node_id": current_id, "error": str(exc)})
            result["partial"] = True
            continue

        props = dict(live_node.get("props") or {})
        previous_enrichment = props.get("enrichment")
        previous_purpose = None
        if isinstance(previous_enrichment, dict):
            raw_previous_purpose = previous_enrichment.get("purpose")
            if isinstance(raw_previous_purpose, str):
                previous_purpose = raw_previous_purpose
        props["enrichment"] = stored
        props["description"] = stored["description"]
        if "purpose" in stored:
            props["purpose"] = stored["purpose"]
        elif props.get("purpose") == previous_purpose:
            props.pop("purpose", None)

        graph.add_node(
            current_id,
            live_node.get("type", "file"),
            live_node.get("label", current_id),
            props,
        )
        tokens_used += max(int(enrichment.tokens_used or 0), 0)
        cost_used += max(float(enrichment.cost_usd or 0.0), 0.0)
        result["enriched"].append(current_id)
        result["usage"] = _usage_block(tokens_used, cost_used)
        changed = True

    if persist and changed:
        graph.save()

    return result


def enrich(
    graph: Graph,
    *,
    provider_name: str | None = None,
    model: str | None = None,
    node_id: str | None = None,
    force: bool = False,
    max_tokens: int | None = None,
    max_cost: float | None = None,
    persist: bool = True,
) -> dict:
    """Resolve a provider and run enrichment against *graph*."""

    resolved_name = _resolve_provider_name(provider_name)
    provider = resolve_provider(resolved_name)
    return run_enrichment(
        graph,
        provider=provider,
        provider_name=resolved_name,
        model=model,
        node_id=node_id,
        force=force,
        max_tokens=max_tokens,
        max_cost=max_cost,
        persist=persist,
    )


def _print_human(result: dict) -> None:
    sys.stdout.write(
        f"Enrichment provider: {result['provider']} ({result['model']})\n"
        f"Requested: {result['requested']}\n"
        f"Enriched: {len(result['enriched'])}\n"
        f"Skipped: {len(result['skipped'])}\n"
        f"Errors: {len(result['errors'])}\n"
        f"Usage: {result['usage']['tokens_used']} tokens, ${result['usage']['cost_usd']:.6f}\n"
    )
    for error in result["errors"]:
        sys.stdout.write(f"- {error['node_id']}: {error['error']}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="wd enrich",
        description="LLM-assisted semantic enrichment for graph nodes.",
    )
    parser.add_argument("--root", type=Path, default=Path("."), help="Project root directory")
    parser.add_argument("--provider", help="Provider name or WELD_ENRICH_PROVIDER env fallback")
    parser.add_argument("--model", help="Override the provider's default model")
    parser.add_argument("--node", dest="node_id", help="Limit enrichment to one node id")
    parser.add_argument("--force", action="store_true", help="Rewrite existing matching enrichment")
    parser.add_argument("--max-tokens", type=_parse_non_negative_int, help="Stop after this many tokens are used")
    parser.add_argument("--max-cost", type=_parse_non_negative_float, help="Stop after this much tracked cost is used")
    parser.add_argument("--json", dest="json_output", action="store_true", default=False, help="Emit machine-readable JSON")
    args = parser.parse_args(argv)

    graph = Graph(args.root)
    graph.load()
    try:
        result = enrich(
            graph,
            provider_name=args.provider,
            model=args.model,
            node_id=args.node_id,
            force=args.force,
            max_tokens=args.max_tokens,
            max_cost=args.max_cost,
            persist=True,
        )
    except ValueError as exc:
        sys.stderr.write(f"wd enrich: {exc}\n")
        return 1
    except RuntimeError as exc:
        sys.stderr.write(f"wd enrich: {exc}\n")
        return 1

    if args.json_output:
        json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        _print_human(result)
    return 0
