"""Regression: known-extension paths must not be truncated at extraction.

``_PATH_RE`` used to lack a trailing word boundary, so greedy
backtracking let a shorter known extension match as a prefix of a longer
one: ``.tsv`` was captured as ``.ts`` and ``.jsonl`` as ``.json``, and
the truncated path was then reported as a broken reference even though
the real file exists (bd 7rmx, reported from the field against v0.22.0).
"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from weld.agent_graph_discovery import discover_agent_graph


def _discover_with_body(body: str, files: tuple[str, ...]) -> dict:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for rel in files:
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("content\n", encoding="utf-8")
        agent = root / ".github" / "agents" / "planner.agent.md"
        agent.parent.mkdir(parents=True, exist_ok=True)
        agent.write_text(
            textwrap.dedent(
                """\
                ---
                name: planner
                description: Produces implementation plans.
                ---

                """
            ) + body + "\n",
            encoding="utf-8",
        )
        return discover_agent_graph(
            root, git_sha="abc123", updated_at="2026-04-24T00:00:00+00:00",
        )


def _broken_references(graph: dict) -> list[str]:
    return [
        item["reference"]
        for item in graph["meta"]["diagnostics"]
        if item["code"] == "agent_graph_broken_reference"
    ]


def _file_reference_targets(graph: dict) -> set[str]:
    return {e["to"] for e in graph["edges"] if e["type"] == "references_file"}


class PathExtensionBoundaryTest(unittest.TestCase):
    def test_tsv_reference_is_not_truncated_to_ts(self) -> None:
        graph = _discover_with_body(
            "Track work in docs/requirement-test-matrix.tsv today.",
            ("docs/requirement-test-matrix.tsv",),
        )
        self.assertEqual(_broken_references(graph), [])
        targets = _file_reference_targets(graph)
        self.assertTrue(
            any(t.endswith("requirement-test-matrix.tsv") for t in targets),
            targets,
        )
        self.assertFalse(
            any(t.endswith("requirement-test-matrix.ts") for t in targets),
            targets,
        )

    def test_jsonl_reference_is_not_truncated_to_json(self) -> None:
        graph = _discover_with_body(
            "The ledger lives at .ledger/events.jsonl in this repo.",
            (".ledger/events.jsonl",),
        )
        self.assertEqual(_broken_references(graph), [])
        targets = _file_reference_targets(graph)
        self.assertTrue(
            any(t.endswith("events.jsonl") for t in targets), targets,
        )
        self.assertFalse(
            any(t.endswith("events.json") for t in targets), targets,
        )

    def test_missing_tsv_is_reported_with_its_full_extension(self) -> None:
        graph = _discover_with_body(
            "See docs/never-written.tsv for the matrix.", (),
        )
        self.assertEqual(_broken_references(graph), ["docs/never-written.tsv"])

    def test_plain_md_reference_still_extracts(self) -> None:
        graph = _discover_with_body(
            "Read docs/architecture.md before planning.",
            ("docs/architecture.md",),
        )
        self.assertEqual(_broken_references(graph), [])
        self.assertTrue(
            any(
                t.endswith("docs-architecture.md")
                for t in _file_reference_targets(graph)
            ),
        )

    def test_extension_followed_by_word_char_is_not_a_reference(self) -> None:
        # docs/data.json_backup must not be extracted as docs/data.json.
        graph = _discover_with_body(
            "Archived copy sits in docs/data.json_backup here.", (),
        )
        self.assertEqual(_broken_references(graph), [])


if __name__ == "__main__":
    unittest.main()
