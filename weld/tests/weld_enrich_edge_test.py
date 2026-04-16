"""Tests for cross-repo edge enrichment."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.enrich_edges import run_edge_enrichment  # noqa: E402
from weld.graph import Graph  # noqa: E402
from weld.providers import EnrichmentResult, build_edge_prompt  # noqa: E402

_SEP = "\x1f"
_FROM = f"api{_SEP}http_client:call-auth"
_TO = f"auth{_SEP}route:POST:/tokens"


def _write_graph(root: Path, nodes: dict, edges: list[dict] | None = None) -> Graph:
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "graph.json").write_text(json.dumps({
        "meta": {"version": 4, "updated_at": "2026-04-14T00:00:00+00:00", "schema_version": 2},
        "nodes": nodes, "edges": edges or [],
    }, indent=2) + "\n", encoding="utf-8")
    g = Graph(root)
    g.load()
    return g


def _node(ntype: str, label: str, **props: object) -> dict:
    return {"type": ntype, "label": label, "props": props}


def _xedge(from_c: str, from_n: str, to_c: str, to_n: str,
           etype: str = "cross_repo:calls", **props: object) -> dict:
    return {"from": f"{from_c}{_SEP}{from_n}", "to": f"{to_c}{_SEP}{to_n}",
            "type": etype, "props": dict(props)}


# Default pair of federated nodes used by most tests.
_PAIR_NODES = {
    _FROM: _node("http_client", "POST /tokens"),
    _TO: _node("route", "POST /tokens"),
}


class _Stub:
    """Canned provider stub -- returns a fixed description per edge key."""
    DEFAULT_MODEL = "stub-edge-model"

    def __init__(self, responses: dict[str, str] | None = None) -> None:
        self._responses = responses or {}
        self.calls: list[tuple[dict, str]] = []

    def enrich(self, node: dict, neighbors: list[dict], *, model: str) -> EnrichmentResult:
        key = f"{node.get('id', '')}|{node.get('type', '')}"
        self.calls.append((node, model))
        return EnrichmentResult(
            description=self._responses.get(key, "Default edge description."),
            tokens_used=10, cost_usd=0.001,
        )


def _run(graph: Graph, provider: _Stub | None = None, **kw) -> dict:
    provider = provider or _Stub()
    kw.setdefault("persist", False)
    kw.setdefault("provider_name", "stub")
    return run_edge_enrichment(graph, provider=provider, **kw)


class WeldEnrichEdgeTest(unittest.TestCase):
    def test_enriches_cross_repo_edge_with_description(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            graph = _write_graph(Path(tmp), dict(_PAIR_NODES), [
                _xedge("api", "http_client:call-auth", "auth", "route:POST:/tokens",
                       source_strategy="service_graph", method="POST", path="/tokens"),
            ])
            result = _run(graph)
            self.assertEqual(result["enriched_edges"], 1)
            self.assertFalse(result["partial"])
            edge = graph.dump()["edges"][0]
            self.assertTrue(edge["props"]["description"].strip())

    def test_edge_structure_unchanged_after_enrichment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            orig = _xedge("api", "http_client:call-auth", "auth", "route:POST:/tokens",
                          source_strategy="service_graph", method="POST", path="/tokens", host="auth")
            graph = _write_graph(Path(tmp), dict(_PAIR_NODES), [orig])
            _run(graph)
            e = graph.dump()["edges"][0]
            self.assertEqual(e["from"], orig["from"])
            self.assertEqual(e["to"], orig["to"])
            self.assertEqual(e["type"], orig["type"])
            for k in ("source_strategy", "method", "path", "host"):
                self.assertEqual(e["props"][k], orig["props"][k])

    def test_idempotent_enrichment_no_edge_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            graph = _write_graph(Path(tmp), dict(_PAIR_NODES), [
                _xedge("api", "http_client:call-auth", "auth", "route:POST:/tokens"),
            ])
            _run(graph)
            count_1 = len(graph.dump()["edges"])
            _run(graph)
            self.assertEqual(len(graph.dump()["edges"]), count_1)

    def test_canned_llm_produces_deterministic_description(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            edge = _xedge("api", "http_client:call-auth", "auth", "route:POST:/tokens")
            key = f"{edge['from']}->{edge['to']}|cross_repo:calls"
            expected = "API authenticates via auth service token endpoint."
            graph = _write_graph(Path(tmp), dict(_PAIR_NODES), [edge])
            _run(graph, provider=_Stub({key: expected}))
            self.assertEqual(graph.dump()["edges"][0]["props"]["description"], expected)

    def test_local_edges_not_enriched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            graph = _write_graph(Path(tmp),
                {"entity:Store": _node("entity", "Store"), "route:ls": _node("route", "LS")},
                [{"from": "route:ls", "to": "entity:Store", "type": "depends_on", "props": {}}])
            result = _run(graph)
            self.assertEqual(result["enriched_edges"], 0)
            self.assertNotIn("description", graph.dump()["edges"][0].get("props", {}))

    def test_edge_enrichment_respects_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            graph = _write_graph(Path(tmp), {
                f"api{_SEP}http_client:c1": _node("http_client", "GET /a"),
                f"api{_SEP}http_client:c2": _node("http_client", "GET /b"),
                f"auth{_SEP}route:GET:/a": _node("route", "GET /a"),
                f"auth{_SEP}route:GET:/b": _node("route", "GET /b"),
            }, [
                _xedge("api", "http_client:c1", "auth", "route:GET:/a"),
                _xedge("api", "http_client:c2", "auth", "route:GET:/b"),
            ])
            result = _run(graph, max_tokens=10)
            self.assertEqual(result["enriched_edges"], 1)
            self.assertTrue(result["partial"])

    def test_edge_enrichment_skips_cached(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            graph = _write_graph(Path(tmp), dict(_PAIR_NODES), [{
                "from": _FROM, "to": _TO, "type": "cross_repo:calls",
                "props": {"description": "Existing.", "enrichment": {
                    "provider": "stub", "model": "stub-edge-model",
                    "timestamp": "2026-04-14T00:00:00+00:00"}},
            }])
            stub = _Stub()
            result = _run(graph, provider=stub)
            self.assertEqual(result["skipped_edges"], 1)
            self.assertEqual(len(stub.calls), 0)

    def test_force_overrides_cached_enrichment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            graph = _write_graph(Path(tmp), dict(_PAIR_NODES), [{
                "from": _FROM, "to": _TO, "type": "cross_repo:calls",
                "props": {"description": "Old.", "enrichment": {
                    "provider": "stub", "model": "stub-edge-model",
                    "timestamp": "2026-04-14T00:00:00+00:00"}},
            }])
            stub = _Stub()
            result = _run(graph, provider=stub, force=True)
            self.assertEqual(result["enriched_edges"], 1)
            self.assertEqual(len(stub.calls), 1)


class WeldBuildEdgePromptTest(unittest.TestCase):
    def test_prompt_contains_edge_and_endpoints(self) -> None:
        edge = {"from": _FROM, "to": _TO, "type": "cross_repo:calls",
                "props": {"method": "POST", "path": "/tokens"}}
        fn = {"id": _FROM, "type": "http_client", "label": "POST /tokens", "props": {}}
        tn = {"id": _TO, "type": "route", "label": "POST /tokens", "props": {}}
        prompt = build_edge_prompt(edge, fn, tn)
        self.assertIn("cross-repo edge", prompt.lower())
        self.assertIn("cross_repo:calls", prompt)
        self.assertIn("http_client:call-auth", prompt)

    def test_prompt_asks_for_json_description(self) -> None:
        edge = {"from": "a\x1fb", "to": "c\x1fd", "type": "cross_repo:calls", "props": {}}
        n = {"id": "x", "type": "t", "label": "l", "props": {}}
        prompt = build_edge_prompt(edge, n, n)
        self.assertIn("description", prompt.lower())
        self.assertIn("JSON", prompt)


if __name__ == "__main__":
    unittest.main()
