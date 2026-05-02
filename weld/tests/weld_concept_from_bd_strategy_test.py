"""Tests for the concept_from_bd discovery strategy.

The strategy reads a JSON-lines issue tracker file and emits one
``concept`` node for each open issue carrying the dogfood-gap label,
plus ``relates_to`` edges to repo-relative file paths cited inside the
description.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from weld.strategies._helpers import StrategyResult
from weld.strategies.concept_from_bd import (
    _candidate_node_ids,
    _cited_paths,
    _slugify_concept,
    extract,
)


# Generic in-tmpdir relative path for the issue store. The strategy reads
# whatever path is supplied via source['path']; the deployed config wires
# the real internal location, but unit tests deliberately use a neutral
# filename so the source/test files do not embed the production literal
# (this keeps the publish-audit danger-pattern policy from flagging tests
# of an internal-source-only strategy).
_TEST_ISSUES_REL = "issues.jsonl"


def _write_issues(
    root: Path,
    payloads: list[dict],
    rel: str = _TEST_ISSUES_REL,
) -> Path:
    """Write a JSON-lines issues file at *root/rel* and return its path.

    The relative path is parameterizable so a test can exercise nested
    subdirectories without committing a specific deployment-level
    convention into source.
    """
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for payload in payloads:
            fh.write(json.dumps(payload) + "\n")
    return path


# ---------------------------------------------------------------------------
# slug normalization
# ---------------------------------------------------------------------------


class TestSlugifyConcept(unittest.TestCase):
    """Slug derivation must be bounded and safe for use in a node id."""

    def test_strips_known_prefix(self) -> None:
        self.assertEqual(
            _slugify_concept("weld dogfood gap: dogfood loop empty"),
            "dogfood-loop-empty",
        )

    def test_strips_uppercase_prefix(self) -> None:
        self.assertEqual(
            _slugify_concept("WELD DOGFOOD GAP: telemetry signal"),
            "telemetry-signal",
        )

    def test_collapses_runs_of_punctuation(self) -> None:
        self.assertEqual(
            _slugify_concept("weld dogfood gap: foo!! / bar.. baz"),
            "foo-bar-baz",
        )

    def test_truncates_long_titles(self) -> None:
        long_title = "weld dogfood gap: " + "a" * 500
        slug = _slugify_concept(long_title)
        self.assertLessEqual(len(slug), 80)
        self.assertEqual(slug, "a" * 80)

    def test_drops_non_ascii(self) -> None:
        slug = _slugify_concept("weld dogfood gap: cafe noir")
        self.assertTrue(
            all(c.islower() or c.isdigit() or c == "-" for c in slug),
            f"unexpected character in slug: {slug!r}",
        )
        self.assertIn("cafe", slug)
        self.assertIn("noir", slug)

    def test_falls_back_when_empty(self) -> None:
        # An issue title that reduces to nothing must still produce a
        # stable non-empty slug so the node id is well-formed.
        self.assertEqual(_slugify_concept("weld dogfood gap: !!!"), "untitled")


# ---------------------------------------------------------------------------
# cited-path extraction (path-traversal hardened)
# ---------------------------------------------------------------------------


class TestCandidateNodeIds(unittest.TestCase):
    """Each cited path expands to several plausible target spellings."""

    def test_python_module_path_yields_stem_form(self) -> None:
        ids = _candidate_node_ids("weld/discover.py")
        self.assertIn("file:weld/discover.py", ids)
        self.assertIn("file:discover", ids)

    def test_root_config_yields_config_form(self) -> None:
        ids = _candidate_node_ids("CLAUDE.md")
        self.assertIn("file:CLAUDE.md", ids)
        self.assertIn("config:CLAUDE_md", ids)

    def test_dotfile_normalization(self) -> None:
        ids = _candidate_node_ids(".bazelrc")
        # Leading dot stripped, no dot in the safe name.
        self.assertIn("config:bazelrc", ids)


class TestCitedPaths(unittest.TestCase):
    """Cited paths must be repo-relative and may not escape the root."""

    def test_extracts_simple_repo_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "weld").mkdir()
            (root / "weld" / "discover.py").write_text("# stub")
            paths = _cited_paths(root, "See weld/discover.py for context")
            self.assertIn("weld/discover.py", paths)

    def test_rejects_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = _cited_paths(root, "See /etc/passwd for ideas")
            self.assertNotIn("/etc/passwd", paths)

    def test_rejects_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "child"
            root.mkdir()
            paths = _cited_paths(root, "Look at ../outside/file.py")
            self.assertNotIn("../outside/file.py", paths)
            self.assertFalse(any(".." in p for p in paths))

    def test_only_existing_paths_are_kept(self) -> None:
        # _cited_paths only emits edges for paths that actually exist in
        # the worktree, preventing the graph from growing dangling
        # edges on stale issue text.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "real.md").write_text("hi")
            paths = _cited_paths(root, "real.md and missing.md")
            self.assertIn("real.md", paths)
            self.assertNotIn("missing.md", paths)


# ---------------------------------------------------------------------------
# end-to-end strategy
# ---------------------------------------------------------------------------


class TestExtract(unittest.TestCase):
    """End-to-end behavior of the discovery strategy."""

    def test_emits_concept_node_for_open_dogfood_gap(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "CLAUDE.md").write_text("# project policy")
            _write_issues(
                root,
                [
                    {
                        "id": "repo-foo-1234-aaaa",
                        "title": "weld dogfood gap: dogfood loop concept missing",
                        "description": (
                            "Cited file: CLAUDE.md is the policy doc.\n"
                            "Tool used: wd query"
                        ),
                        "status": "open",
                        "priority": 3,
                        "labels": ["weld-dogfood-gap"],
                    }
                ],
            )

            source = {
                "strategy": "concept_from_bd",
                "path": _TEST_ISSUES_REL,
            }
            result = extract(root, source, {})

            self.assertIsInstance(result, StrategyResult)
            ids = list(result.nodes.keys())
            self.assertEqual(len(ids), 1)
            nid = ids[0]
            self.assertTrue(nid.startswith("concept:"))
            self.assertEqual(nid, "concept:dogfood-loop-concept-missing")
            node = result.nodes[nid]
            self.assertEqual(node["type"], "concept")
            props = node["props"]
            self.assertEqual(props["source_strategy"], "concept_from_bd")
            self.assertEqual(props["authority"], "derived")
            self.assertEqual(props["confidence"], "inferred")
            self.assertIn("dogfood loop", props["description"].lower())
            self.assertEqual(props["bd_short_id"], "aaaa")
            edge_targets = {
                (e["from"], e["type"], e["to"]) for e in result.edges
            }
            # The strategy emits one edge per plausible target spelling
            # so it lands on whichever convention the file-emitting
            # strategy used (file:<rel>, file:<stem>, config:<safe>).
            self.assertIn((nid, "relates_to", "file:CLAUDE.md"), edge_targets)
            self.assertIn((nid, "relates_to", "file:CLAUDE"), edge_targets)
            self.assertIn(
                (nid, "relates_to", "config:CLAUDE_md"), edge_targets
            )

    def test_skips_closed_issues(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_issues(
                root,
                [
                    {
                        "id": "repo-foo-1234-bbbb",
                        "title": "weld dogfood gap: already-fixed concept",
                        "description": "x",
                        "status": "closed",
                        "labels": ["weld-dogfood-gap"],
                    }
                ],
            )
            source = {
                "strategy": "concept_from_bd",
                "path": _TEST_ISSUES_REL,
            }
            result = extract(root, source, {})
            self.assertEqual(result.nodes, {})

    def test_skips_issues_without_dogfood_label(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_issues(
                root,
                [
                    {
                        "id": "repo-foo-1234-cccc",
                        "title": "weld dogfood gap: looks-similar-but-mislabeled",
                        "description": "x",
                        "status": "open",
                        "labels": ["bug"],
                    }
                ],
            )
            source = {
                "strategy": "concept_from_bd",
                "path": _TEST_ISSUES_REL,
            }
            result = extract(root, source, {})
            self.assertEqual(result.nodes, {})

    def test_returns_empty_when_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = {
                "strategy": "concept_from_bd",
                "path": _TEST_ISSUES_REL,
            }
            result = extract(root, source, {})
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])

    def test_skips_malformed_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "CLAUDE.md").write_text("hi")
            issues_path = root / _TEST_ISSUES_REL
            issues_path.parent.mkdir(parents=True, exist_ok=True)
            issues_path.write_text(
                "this is not json\n"
                + json.dumps(
                    {
                        "id": "repo-foo-1234-dddd",
                        "title": "weld dogfood gap: survives",
                        "description": "CLAUDE.md",
                        "status": "open",
                        "labels": ["weld-dogfood-gap"],
                    }
                )
                + "\n"
            )
            source = {
                "strategy": "concept_from_bd",
                "path": _TEST_ISSUES_REL,
            }
            result = extract(root, source, {})
            self.assertTrue(
                any(nid.startswith("concept:") for nid in result.nodes),
                f"valid line did not yield a concept node: {result.nodes}",
            )

    def test_no_internal_id_leaks_into_node(self) -> None:
        # Regression for publish-audit hygiene: never embed the full id.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_issues(
                root,
                [
                    {
                        "id": "alpha-beta-9999-eeee",
                        "title": "weld dogfood gap: x",
                        "description": "y",
                        "status": "open",
                        "labels": ["weld-dogfood-gap"],
                    }
                ],
            )
            source = {
                "strategy": "concept_from_bd",
                "path": _TEST_ISSUES_REL,
            }
            result = extract(root, source, {})
            blob = json.dumps(
                {"nodes": result.nodes, "edges": result.edges}
            )
            self.assertNotIn("alpha-beta-9999-eeee", blob)
            self.assertNotIn("alpha-beta", blob)
            self.assertIn("eeee", blob)

    def test_dropped_traversal_path_does_not_emit_edge(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_issues(
                root,
                [
                    {
                        "id": "repo-foo-1234-ffff",
                        "title": "weld dogfood gap: traversal probe",
                        "description": "../escape.md",
                        "status": "open",
                        "labels": ["weld-dogfood-gap"],
                    }
                ],
            )
            source = {
                "strategy": "concept_from_bd",
                "path": _TEST_ISSUES_REL,
            }
            result = extract(root, source, {})
            self.assertEqual(result.edges, [])

    def test_returns_empty_when_path_key_missing(self) -> None:
        # Pins the no-default contract: the strategy refuses to read any
        # implicit location and instead returns an empty result when the
        # caller did not supply source['path']. This keeps the production
        # storage location out of source as a hard-coded literal and
        # forces the deployed config (discover.yaml) to name it
        # explicitly.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = {"strategy": "concept_from_bd"}
            result = extract(root, source, {})
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])


if __name__ == "__main__":
    unittest.main()
