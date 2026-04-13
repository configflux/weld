"""Tests for the runtime_contract linkage strategy."""

from __future__ import annotations

import tempfile
from pathlib import Path

from cortex.contract import validate_fragment
from cortex.strategies._helpers import StrategyResult
from cortex.strategies.runtime_contract import extract

_RUNTIME_DOC = """\
# Runtime Contract

## Runtime Summary

| Boundary | Runtime shape | Reachability | Health or entrypoint | Required inputs today |
| --- | --- | --- | --- | --- |
| `web` | Long-lived Next.js HTTP container | Public HTTPS edge | HTTP response from the shell routes | `FOO` env var |
| `api` | Long-lived FastAPI HTTP container | Public routes only | `GET /healthz`, `GET /readyz` | `BAR` env var |
| `worker` | Run-to-completion Python container | Never public | stage entrypoint exit status | stage-specific env vars |
| `postgres` | Managed PostgreSQL | Private only | provider health | connection string |

## Key Environment Variables

Irrelevant section.
"""

_UNRELATED_DOC = """\
# Some other guide

No runtime summary here.
"""

def _write_runtime_doc(root: Path) -> Path:
    docs = root / "docs"
    docs.mkdir()
    path = docs / "runtime-contract.md"
    path.write_text(_RUNTIME_DOC)
    return path

class TestRuntimeContractExtract:
    """Tests for runtime_contract strategy extract()."""

    def test_returns_strategy_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_runtime_doc(root)
            result = extract(root, {"glob": "docs/*.md"}, {})
            assert isinstance(result, StrategyResult)

    def test_health_endpoints_become_rpc_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_runtime_doc(root)
            result = extract(root, {"glob": "docs/*.md"}, {})
            rpc_nodes = {k: v for k, v in result.nodes.items() if v["type"] == "rpc"}
            assert len(rpc_nodes) == 2
            labels = {v["label"] for v in rpc_nodes.values()}
            assert labels == {"GET /healthz", "GET /readyz"}

    def test_rpc_nodes_carry_full_interaction_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_runtime_doc(root)
            result = extract(root, {"glob": "docs/*.md"}, {})
            for nid, node in result.nodes.items():
                if node["type"] != "rpc":
                    continue
                props = node["props"]
                assert props["source_strategy"] == "runtime_contract"
                assert props["authority"] == "canonical"
                assert props["confidence"] == "definite"
                assert props["protocol"] == "http"
                assert props["surface_kind"] == "request_response"
                assert props["transport"] == "http"
                assert props["boundary_kind"] == "inbound"
                assert props["declared_in"] == "docs/runtime-contract.md"
                assert props["file"] == "docs/runtime-contract.md"

    def test_doc_documents_each_known_boundary_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_runtime_doc(root)
            result = extract(root, {"glob": "docs/*.md"}, {})
            doc_id = "doc:guide/runtime-contract"
            doc_edges = [
                e for e in result.edges
                if e["from"] == doc_id and e["type"] == "documents"
            ]
            targets = {e["to"] for e in doc_edges}
            assert "service:api" in targets
            assert "service:web" in targets
            assert "service:worker" in targets

    def test_api_service_exposes_health_rpc_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_runtime_doc(root)
            result = extract(root, {"glob": "docs/*.md"}, {})
            exposes = [
                e for e in result.edges
                if e["from"] == "service:api" and e["type"] == "exposes"
            ]
            assert len(exposes) == 2
            for e in exposes:
                assert e["to"].startswith("rpc:runtime-contract/")
                assert e["props"]["source_strategy"] == "runtime_contract"

    def test_verification_gates_link_to_runtime_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_runtime_doc(root)
            result = extract(root, {"glob": "docs/*.md"}, {})
            doc_id = "doc:guide/runtime-contract"
            gate_edges = [
                e for e in result.edges
                if e["type"] == "verifies" and e["to"] == doc_id
            ]
            froms = {e["from"] for e in gate_edges}
            assert "gate:local-task-gate" in froms
            assert "gate:run-e2e" in froms

    def test_deploy_context_hook_emits_relates_to_edges(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_runtime_doc(root)
            context = {"deploy_node_ids": ["deploy:docker_compose"]}
            result = extract(root, {"glob": "docs/*.md"}, context)
            doc_id = "doc:guide/runtime-contract"
            rel_edges = [
                e for e in result.edges
                if e["type"] == "relates_to" and e["to"] == doc_id
            ]
            assert len(rel_edges) == 1
            assert rel_edges[0]["from"] == "deploy:docker_compose"

    def test_unrelated_markdown_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            docs = root / "docs"
            docs.mkdir()
            (docs / "other.md").write_text(_UNRELATED_DOC)
            result = extract(root, {"glob": "docs/*.md"}, {})
            assert result.nodes == {}
            assert result.edges == []
            assert result.discovered_from == []

    def test_missing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            result = extract(root, {"glob": "docs/*.md"}, {})
            assert result.nodes == {}
            assert result.edges == []

    def test_no_glob_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_runtime_doc(root)
            result = extract(root, {}, {})
            assert result.nodes == {}
            assert result.edges == []

    def test_discovered_from_populated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_runtime_doc(root)
            result = extract(root, {"glob": "docs/*.md"}, {})
            assert "docs/runtime-contract.md" in result.discovered_from

    def test_fragment_validates_against_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_runtime_doc(root)
            result = extract(root, {"glob": "docs/*.md"}, {})
            fragment = {
                "nodes": result.nodes,
                "edges": result.edges,
                "discovered_from": result.discovered_from,
            }
            errors = validate_fragment(
                fragment,
                source_label="strategy:runtime_contract",
                allow_dangling_edges=True,
            )
            assert errors == [], [str(e) for e in errors]

    def test_exclude_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_runtime_doc(root)
            result = extract(
                root,
                {"glob": "docs/*.md", "exclude": ["runtime-contract.md"]},
                {},
            )
            assert result.nodes == {}
            assert result.edges == []
