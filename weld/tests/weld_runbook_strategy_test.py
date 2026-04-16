"""Tests for the runbook extraction strategy."""

from __future__ import annotations

import tempfile
from pathlib import Path

from weld.strategies._helpers import StrategyResult
from weld.strategies.runbook import extract

_ACQUISITION_RUNBOOK = """\
# Acquisition Worker Runbook

This runbook covers the bounded acquisition worker added in `project-67v.9`.

## Runtime Contract

Required inputs:

- `--registry-path`: JSON source registry
- `--artifact-dir`: output directory

## Troubleshooting

Check the worker logs for acquisition errors.
"""

_EXTRACTION_RUNBOOK = """\
# Extraction Worker Runbook

This runbook covers the extraction worker stage.

## Service

The extraction worker processes raw artifacts from the acquisition stage.

## Common Issues

- Memory pressure during large HTML parsing
- Timeout on slow retailer sites
"""

class TestRunbookExtract:
    """Tests for runbook strategy extract()."""

    def test_extracts_runbook_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            rb_dir = root / "docs" / "runbooks"
            rb_dir.mkdir(parents=True)
            (rb_dir / "acquisition_worker.md").write_text(_ACQUISITION_RUNBOOK)

            source = {"glob": "docs/runbooks/*.md"}
            result = extract(root, source, {})

            assert isinstance(result, StrategyResult)
            assert len(result.nodes) == 1
            nid = list(result.nodes.keys())[0]
            node = result.nodes[nid]
            assert node["type"] == "runbook"

    def test_runbook_label_from_heading(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            rb_dir = root / "docs" / "runbooks"
            rb_dir.mkdir(parents=True)
            (rb_dir / "acquisition_worker.md").write_text(_ACQUISITION_RUNBOOK)

            source = {"glob": "docs/runbooks/*.md"}
            result = extract(root, source, {})

            node = list(result.nodes.values())[0]
            assert node["label"] == "Acquisition Worker Runbook"

    def test_normalized_metadata_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            rb_dir = root / "docs" / "runbooks"
            rb_dir.mkdir(parents=True)
            (rb_dir / "acquisition_worker.md").write_text(_ACQUISITION_RUNBOOK)

            source = {"glob": "docs/runbooks/*.md"}
            result = extract(root, source, {})

            for nid, node in result.nodes.items():
                props = node["props"]
                assert props["source_strategy"] == "runbook"
                assert props["authority"] == "canonical"
                assert props["confidence"] == "definite"
                assert isinstance(props["roles"], list)
                assert "doc" in props["roles"]
                assert props["doc_kind"] == "runbook"

    def test_multiple_runbooks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            rb_dir = root / "docs" / "runbooks"
            rb_dir.mkdir(parents=True)
            (rb_dir / "acquisition_worker.md").write_text(_ACQUISITION_RUNBOOK)
            (rb_dir / "extraction_worker.md").write_text(_EXTRACTION_RUNBOOK)

            source = {"glob": "docs/runbooks/*.md"}
            result = extract(root, source, {})

            assert len(result.nodes) == 2
            assert len(result.discovered_from) == 2

    def test_skips_readme(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            rb_dir = root / "docs" / "runbooks"
            rb_dir.mkdir(parents=True)
            (rb_dir / "README.md").write_text("# Runbooks index\n")
            (rb_dir / "acquisition_worker.md").write_text(_ACQUISITION_RUNBOOK)

            source = {"glob": "docs/runbooks/*.md"}
            result = extract(root, source, {})

            assert len(result.nodes) == 1

    def test_exclude_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            rb_dir = root / "docs" / "runbooks"
            rb_dir.mkdir(parents=True)
            (rb_dir / "acquisition_worker.md").write_text(_ACQUISITION_RUNBOOK)

            source = {"glob": "docs/runbooks/*.md",
                      "exclude": ["acquisition_worker.md"]}
            result = extract(root, source, {})

            assert len(result.nodes) == 0

    def test_missing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = {"glob": "docs/runbooks/*.md"}
            result = extract(root, source, {})

            assert result.nodes == {}
            assert result.edges == []

    def test_discovered_from_populated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            rb_dir = root / "docs" / "runbooks"
            rb_dir.mkdir(parents=True)
            (rb_dir / "acquisition_worker.md").write_text(_ACQUISITION_RUNBOOK)

            source = {"glob": "docs/runbooks/*.md"}
            result = extract(root, source, {})

            assert "docs/runbooks/acquisition_worker.md" in result.discovered_from

    def test_service_association_edge(self) -> None:
        """Runbooks mentioning a worker stage should produce edges."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            rb_dir = root / "docs" / "runbooks"
            rb_dir.mkdir(parents=True)
            (rb_dir / "acquisition_worker.md").write_text(_ACQUISITION_RUNBOOK)

            source = {"glob": "docs/runbooks/*.md"}
            result = extract(root, source, {})

            # The runbook filename contains "acquisition" which maps to the
            # acquisition stage — an edge should be created
            docs_edges = [e for e in result.edges if e["type"] == "documents"]
            assert len(docs_edges) >= 1

    def test_node_id_uses_runbook_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            rb_dir = root / "docs" / "runbooks"
            rb_dir.mkdir(parents=True)
            (rb_dir / "acquisition_worker.md").write_text(_ACQUISITION_RUNBOOK)

            source = {"glob": "docs/runbooks/*.md"}
            result = extract(root, source, {})

            nid = list(result.nodes.keys())[0]
            assert nid.startswith("runbook:")

    def test_fallback_label_from_filename(self) -> None:
        """When no heading is found, label from filename."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            rb_dir = root / "docs" / "runbooks"
            rb_dir.mkdir(parents=True)
            (rb_dir / "replay_operations.md").write_text(
                "Some text without a heading.\n"
            )

            source = {"glob": "docs/runbooks/*.md"}
            result = extract(root, source, {})

            node = list(result.nodes.values())[0]
            assert "Replay" in node["label"]
