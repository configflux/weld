"""Tests for section-level markdown extraction with role tagging.

Verifies that the markdown strategy extracts section-level nodes from
headings, tags them with section_kind, includes spans, links them to
their parent doc via contains edges, and passes contract validation.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from cortex.strategies.markdown import extract

_ADR_DOC = """\
# ADR 0007: Use Postgres for Analytics

## Context

We need a persistent store for analytics events.

## Decision

Use Postgres with a partitioned table.

## Consequences

- Higher operational cost
- Familiar tooling
"""

_GUIDE_DOC = """\
# Getting Started

## Installation

Run `pip install myapp`.

## Configuration

Set the following environment variables:

- `DATABASE_URL`
- `REDIS_URL`

## API Reference

See the endpoint docs below.

## Troubleshooting

### Common errors

Check the logs first.

### Network issues

Retry with exponential backoff.
"""

_ARCHITECTURE_DOC = """\
# System Architecture

## Overview

The system uses a microservices architecture.

## Components

### API Service

Handles HTTP requests.

## Deployment

Deployed via Docker Compose.
"""

_GUIDE_SOURCE = {
    "glob": "docs/*.md",
    "id_prefix": "doc:guide",
    "doc_kind": "guide",
    "extract_sections": True,
}

def _extract_guide(root: Path, text: str, **overrides) -> tuple:
    """Helper: write text to docs/guide.md, run extract, return result."""
    docs = root / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "guide.md").write_text(text)
    source = {**_GUIDE_SOURCE, **overrides}
    return extract(root, source, {})

class TestSectionExtraction:
    """Section-level extraction from markdown headings."""

    def test_sections_enabled_produces_section_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            result = _extract_guide(Path(d), _GUIDE_DOC)
            assert len(result.nodes) > 1, (
                f"expected section nodes, got {len(result.nodes)}"
            )

    def test_sections_disabled_no_section_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            result = _extract_guide(
                Path(d), _GUIDE_DOC, extract_sections=False,
            )
            assert len(result.nodes) == 1

    def test_section_node_has_span(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            result = _extract_guide(Path(d), _GUIDE_DOC)
            spanned = [
                n for n in result.nodes.values()
                if "span" in n.get("props", {})
            ]
            assert len(spanned) > 0
            for node in spanned:
                span = node["props"]["span"]
                assert span["start_line"] <= span["end_line"]

    def test_section_node_has_section_kind(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            result = _extract_guide(Path(d), _GUIDE_DOC)
            kinds = {
                n["props"].get("section_kind")
                for n in result.nodes.values()
                if n["props"].get("section_kind")
            }
            assert "setup" in kinds, f"got {kinds}"
            assert "configuration" in kinds, f"got {kinds}"
            assert "troubleshooting" in kinds, f"got {kinds}"

    def test_contains_edges_from_doc_to_sections(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            result = _extract_guide(Path(d), _GUIDE_DOC)
            contains = [e for e in result.edges if e["type"] == "contains"]
            assert len(contains) > 0
            for edge in contains:
                assert edge["from"] == "doc:guide/guide"

    def test_no_headings_no_sections(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            result = _extract_guide(Path(d), "Plain text, no headings.\n")
            assert len(result.nodes) == 1
            assert len(result.edges) == 0

    def test_single_h1_no_sections(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            result = _extract_guide(Path(d), "# Title\n\nBody text only.\n")
            assert len(result.nodes) == 1

    def test_section_inherits_authority(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            adrs = root / "docs" / "adrs"
            adrs.mkdir(parents=True)
            (adrs / "0007-analytics.md").write_text(_ADR_DOC)
            result = extract(root, {
                "glob": "docs/adrs/*.md",
                "id_prefix": "doc:adr",
                "doc_kind": "adr",
                "extract_sections": True,
            }, {})
            for nid, node in result.nodes.items():
                assert node["props"]["authority"] == "canonical", nid

    def test_section_node_id_contains_doc_stem(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            result = _extract_guide(Path(d), _GUIDE_DOC)
            section_ids = [
                nid for nid in result.nodes if nid != "doc:guide/guide"
            ]
            for sid in section_ids:
                assert "guide" in sid, sid

    def test_architecture_overview_detected(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            docs = root / "docs"
            docs.mkdir()
            (docs / "arch.md").write_text(_ARCHITECTURE_DOC)
            result = extract(root, {
                "glob": "docs/*.md",
                "id_prefix": "doc:guide",
                "doc_kind": "guide",
                "extract_sections": True,
            }, {})
            kinds = {
                n["props"].get("section_kind")
                for n in result.nodes.values()
                if n["props"].get("section_kind")
            }
            assert "overview" in kinds, f"got {kinds}"

class TestSectionContract:
    """Section nodes pass contract validation."""

    def test_section_kind_vocabulary(self) -> None:
        from cortex.contract import SECTION_KIND_VALUES
        assert isinstance(SECTION_KIND_VALUES, frozenset)
        for kind in (
            "setup", "configuration", "api-reference",
            "architecture", "troubleshooting", "overview", "deployment",
        ):
            assert kind in SECTION_KIND_VALUES, kind

    def test_section_kind_in_optional_props(self) -> None:
        from cortex.contract import NODE_OPTIONAL_PROPS
        assert "section_kind" in NODE_OPTIONAL_PROPS

    def test_validate_accepts_valid_section_kind(self) -> None:
        from cortex.contract import validate_node
        node = {
            "type": "doc",
            "label": "Installation",
            "props": {
                "file": "docs/guide.md",
                "section_kind": "setup",
                "authority": "derived",
                "confidence": "inferred",
                "span": {"start_line": 3, "end_line": 8},
            },
        }
        assert validate_node("doc:guide#install", node) == []

    def test_validate_rejects_invalid_section_kind(self) -> None:
        from cortex.contract import validate_node
        node = {
            "type": "doc",
            "label": "Test",
            "props": {"file": "docs/t.md", "section_kind": "bogus"},
        }
        errors = validate_node("doc:t#s", node)
        assert any("section_kind" in str(e) for e in errors), errors

    def test_extracted_nodes_pass_validation(self) -> None:
        from cortex.contract import validate_node
        with tempfile.TemporaryDirectory() as d:
            result = _extract_guide(Path(d), _GUIDE_DOC)
            for nid, node in result.nodes.items():
                errors = validate_node(nid, node)
                assert errors == [], f"{nid}: {errors}"

    def test_extracted_edges_pass_validation(self) -> None:
        from cortex.contract import validate_edge
        with tempfile.TemporaryDirectory() as d:
            result = _extract_guide(Path(d), _GUIDE_DOC)
            node_ids = set(result.nodes.keys())
            for edge in result.edges:
                errors = validate_edge(edge, node_ids)
                assert errors == [], f"edge {edge}: {errors}"
