"""Tests for built-in graph enrichment orchestration."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.enrich import run_enrichment  # noqa: E402
from weld.graph import Graph  # noqa: E402
from weld.providers import EnrichmentResult  # noqa: E402


def _write_graph(root: Path, nodes: dict[str, dict], edges: list[dict] | None = None) -> Graph:
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    graph_path = weld_dir / "graph.json"
    graph_path.write_text(
        json.dumps(
            {
                "meta": {"version": 4, "updated_at": "2026-04-14T00:00:00+00:00"},
                "nodes": nodes,
                "edges": edges or [],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    graph = Graph(root)
    graph.load()
    return graph


def _node(node_type: str, label: str, **props: object) -> dict:
    return {"type": node_type, "label": label, "props": props}


def _stored_enrichment(
    *,
    provider: str,
    model: str,
    description: str,
    purpose: str | None = None,
    fingerprint: str = "stale",
) -> dict:
    enrichment = {
        "provider": provider,
        "model": model,
        "timestamp": "2026-04-14T00:00:00+00:00",
        "fingerprint": fingerprint,
        "description": description,
    }
    if purpose is not None:
        enrichment["purpose"] = purpose
    return enrichment


class StubProvider:
    DEFAULT_MODEL = "stub-model"

    def __init__(self, responses: dict[str, dict]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, list[str], str]] = []

    def enrich(self, node: dict, neighbors: list[dict], *, model: str) -> EnrichmentResult:
        self.calls.append((node["id"], [neighbor["id"] for neighbor in neighbors], model))
        payload = self._responses[node["id"]]
        return EnrichmentResult(
            description=payload["description"],
            purpose=payload.get("purpose"),
            complexity_hint=payload.get("complexity_hint"),
            suggested_tags=payload.get("suggested_tags", []),
            tokens_used=payload.get("tokens_used", 0),
            cost_usd=payload.get("cost_usd", 0.0),
        )


class WeldEnrichTest(unittest.TestCase):
    def test_run_enrichment_writes_nested_and_mirrored_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            graph = _write_graph(
                Path(tmp),
                {
                    "entity:Store": _node("entity", "Store", file="store.py"),
                    "route:list-stores": _node("route", "ListStores", file="routes.py"),
                },
                [
                    {
                        "from": "route:list-stores",
                        "to": "entity:Store",
                        "type": "depends_on",
                        "props": {},
                    },
                ],
            )
            provider = StubProvider(
                {
                    "entity:Store": {
                        "description": "Retail store aggregate.",
                        "purpose": "Represents a storefront location.",
                        "complexity_hint": "medium",
                        "suggested_tags": ["domain", "entity"],
                    },
                }
            )

            result = run_enrichment(
                graph,
                provider=provider,
                provider_name="stub",
                node_id="entity:Store",
            )

            self.assertEqual(result["provider"], "stub")
            self.assertEqual(result["model"], "stub-model")
            self.assertEqual(result["enriched"], ["entity:Store"])
            self.assertEqual(result["skipped"], [])
            self.assertFalse(result["partial"])
            updated = graph.get_node("entity:Store")
            self.assertIsNotNone(updated)
            props = updated["props"]
            self.assertEqual(props["description"], "Retail store aggregate.")
            self.assertEqual(props["purpose"], "Represents a storefront location.")
            self.assertEqual(props["enrichment"]["provider"], "stub")
            self.assertEqual(props["enrichment"]["model"], "stub-model")
            self.assertEqual(props["enrichment"]["complexity_hint"], "medium")
            self.assertEqual(props["enrichment"]["suggested_tags"], ["domain", "entity"])

    def test_run_enrichment_skips_matching_provider_and_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            graph = _write_graph(
                Path(tmp),
                {
                    "entity:Store": _node(
                        "entity",
                        "Store",
                        file="store.py",
                        description="Existing description.",
                        purpose="Existing purpose.",
                        enrichment=_stored_enrichment(
                            provider="stub",
                            model="stub-model",
                            description="Existing description.",
                            purpose="Existing purpose.",
                        ),
                    ),
                },
            )
            provider = StubProvider(
                {"entity:Store": {"description": "New description that should not be used."}}
            )

            initial = run_enrichment(graph, provider=provider, provider_name="stub")
            self.assertEqual(initial["enriched"], ["entity:Store"])
            result = run_enrichment(graph, provider=provider, provider_name="stub")

            self.assertEqual(result["enriched"], [])
            self.assertEqual(result["skipped"], ["entity:Store"])
            self.assertEqual(len(provider.calls), 1)

    def test_run_enrichment_rewrites_when_provider_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            graph = _write_graph(
                Path(tmp),
                {
                    "entity:Store": _node(
                        "entity",
                        "Store",
                        file="store.py",
                        description="Old description.",
                        enrichment=_stored_enrichment(
                            provider="other",
                            model="other-model",
                            description="Old description.",
                        ),
                    ),
                },
            )
            provider = StubProvider({"entity:Store": {"description": "Fresh description."}})

            result = run_enrichment(graph, provider=provider, provider_name="stub")

            self.assertEqual(result["enriched"], ["entity:Store"])
            self.assertEqual(provider.calls[0][0], "entity:Store")
            self.assertEqual(
                graph.get_node("entity:Store")["props"]["enrichment"]["provider"],
                "stub",
            )

    def test_run_enrichment_honors_force_even_with_matching_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            graph = _write_graph(
                Path(tmp),
                {
                    "entity:Store": _node(
                        "entity",
                        "Store",
                        file="store.py",
                        description="Old description.",
                        enrichment=_stored_enrichment(
                            provider="stub",
                            model="stub-model",
                            description="Old description.",
                        ),
                    ),
                },
            )
            provider = StubProvider({"entity:Store": {"description": "Forced rewrite."}})

            result = run_enrichment(
                graph,
                provider=provider,
                provider_name="stub",
                force=True,
            )

            self.assertEqual(result["enriched"], ["entity:Store"])
            self.assertEqual(len(provider.calls), 1)
            self.assertEqual(graph.get_node("entity:Store")["props"]["description"], "Forced rewrite.")

    def test_run_enrichment_reruns_when_snapshot_fingerprint_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            graph = _write_graph(
                Path(tmp),
                {
                    "entity:Store": _node(
                        "entity",
                        "Store",
                        file="store.py",
                        description="Old description.",
                        enrichment=_stored_enrichment(
                            provider="stub",
                            model="stub-model",
                            description="Old description.",
                            fingerprint="stale-fingerprint",
                        ),
                    ),
                    "route:list-stores": _node("route", "ListStores", file="routes.py"),
                },
                [
                    {
                        "from": "route:list-stores",
                        "to": "entity:Store",
                        "type": "depends_on",
                        "props": {},
                    },
                ],
            )
            provider = StubProvider({"entity:Store": {"description": "Fresh description."}})

            result = run_enrichment(
                graph,
                provider=provider,
                provider_name="stub",
                node_id="entity:Store",
            )

            self.assertEqual(result["enriched"], ["entity:Store"])
            self.assertEqual(len(provider.calls), 1)

    def test_run_enrichment_preserves_manual_purpose_when_provider_omits_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            graph = _write_graph(
                Path(tmp),
                {
                    "entity:Store": _node(
                        "entity",
                        "Store",
                        file="store.py",
                        purpose="Manual purpose.",
                        enrichment=_stored_enrichment(
                            provider="stub",
                            model="stub-model",
                            description="Old description.",
                        ),
                    ),
                },
            )
            provider = StubProvider({"entity:Store": {"description": "Fresh description."}})

            result = run_enrichment(
                graph,
                provider=provider,
                provider_name="stub",
                force=True,
            )

            self.assertEqual(result["enriched"], ["entity:Store"])
            self.assertEqual(graph.get_node("entity:Store")["props"]["purpose"], "Manual purpose.")

    def test_run_enrichment_stops_once_budget_is_reached(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            graph = _write_graph(
                Path(tmp),
                {
                    "entity:Account": _node("entity", "Account", file="account.py"),
                    "entity:Store": _node("entity", "Store", file="store.py"),
                },
            )
            provider = StubProvider(
                {
                    "entity:Account": {
                        "description": "Account aggregate.",
                        "tokens_used": 5,
                        "cost_usd": 1.0,
                    },
                    "entity:Store": {
                        "description": "Store aggregate.",
                        "tokens_used": 5,
                        "cost_usd": 1.0,
                    },
                }
            )

            result = run_enrichment(
                graph,
                provider=provider,
                provider_name="stub",
                max_tokens=5,
                max_cost=1.0,
            )

            self.assertEqual(result["enriched"], ["entity:Account"])
            self.assertEqual(result["skipped"], [])
            self.assertEqual(
                result["errors"],
                [{"node_id": "entity:Store", "error": "budget exceeded"}],
            )
            self.assertTrue(result["partial"])
            self.assertEqual(len(provider.calls), 1)

    def test_run_enrichment_processes_nodes_grouped_by_type_then_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            graph = _write_graph(
                Path(tmp),
                {
                    "route:list-stores": _node("route", "ListStores", file="routes.py"),
                    "entity:Account": _node("entity", "Account", file="account.py"),
                    "entity:Store": _node("entity", "Store", file="store.py"),
                },
            )
            provider = StubProvider(
                {
                    "entity:Account": {"description": "Account."},
                    "entity:Store": {"description": "Store."},
                    "route:list-stores": {"description": "List stores route."},
                }
            )

            run_enrichment(graph, provider=provider, provider_name="stub")

            self.assertEqual(
                [call[0] for call in provider.calls],
                ["entity:Account", "entity:Store", "route:list-stores"],
            )


if __name__ == "__main__":
    unittest.main()
