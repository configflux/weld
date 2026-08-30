"""Regression tests for Finding 03: language attribution in both directions.

The v0.23.1 field evaluation showed ``wd capabilities`` mis-attributing
languages two opposite ways, both in
:func:`weld.capabilities.compute_capabilities`:

(a) **False negatives -- ``tree_sitter``.** ``tree_sitter`` declares no
    registry languages; the language it serves is wired per source entry in
    ``discover.yaml`` via ``language:``, which the registry never read. A
    C#-only repo whose every node is ``source_strategy: tree_sitter`` reported
    ``csharp`` all-``no``.

(b) **False positives -- multi-language strategies.** ``test_peer`` declares
    seven languages under one flat extension set; any single matching
    extension flipped *every* declared language, so a Python-only repo
    reported ``csharp/go/java/rust/typescript`` file/tests ``yes``.

These exercise the full registry+graph pipeline ``wd capabilities`` consumes,
from the consumer side, matching the transcript in
``docs/field-reports/weld-0.23.1-findings/transcripts/03-*``.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


from weld.capabilities import compute_capabilities  # noqa: E402
from weld.contract import SCHEMA_VERSION  # noqa: E402


def _make_repo(nodes: dict[str, dict], yaml_text: str) -> Path:
    """Build a tmp repo with a graph + a raw ``discover.yaml`` body."""
    root = Path(tempfile.mkdtemp())
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    (root / ".weld" / "graph.json").write_text(
        json.dumps(
            {
                "meta": {
                    "version": SCHEMA_VERSION,
                    "updated_at": "2026-08-29T00:00:00+00:00",
                },
                "nodes": nodes,
                "edges": [],
            },
        ),
        encoding="utf-8",
    )
    (root / ".weld" / "discover.yaml").write_text(yaml_text, encoding="utf-8")
    return root


def _compute(root: Path) -> dict:
    graph_data = json.loads(
        (root / ".weld" / "graph.json").read_text(encoding="utf-8"),
    )
    return compute_capabilities(graph_data, root)


# Extraction evidence the C#-only transcript proves ``tree_sitter`` supplies.
_TS_EVIDENCE = ("file", "symbols", "imports", "calls")


class TreeSitterPerEntryLanguageTest(unittest.TestCase):
    """(a) ``tree_sitter`` wired via ``language:`` credits that language."""

    def _csharp_tree_sitter_repo(self) -> Path:
        nodes = {
            f"sym:{i}": {
                "type": "symbol",
                "props": {
                    "file": f"src/Component{i}.cs",
                    "source_strategy": "tree_sitter",
                },
            }
            for i in range(29)
        }
        yaml_text = (
            "sources:\n"
            '  - glob: "**/*.cs"\n'
            "    type: file\n"
            "    strategy: tree_sitter\n"
            "    language: csharp\n"
        )
        return _make_repo(nodes, yaml_text)

    def test_tree_sitter_csharp_lights_up_extraction_evidence(self) -> None:
        result = _compute(self._csharp_tree_sitter_repo())
        row = result["languages"]["csharp"]
        for flag in _TS_EVIDENCE:
            self.assertTrue(
                row[flag],
                f"csharp.{flag} must be True when tree_sitter is wired with "
                "language: csharp and the graph holds .cs files",
            )

    def test_tree_sitter_language_gated_on_matching_files(self) -> None:
        """``tree_sitter`` wired for csharp but a graph with only ``.go``
        files must not flip csharp -- the language is credited only when a
        file of that language is present."""
        nodes = {
            "file:main": {
                "type": "file",
                "props": {"file": "main.go", "source_strategy": "tree_sitter"},
            },
        }
        yaml_text = (
            "sources:\n"
            '  - glob: "**/*.cs"\n'
            "    type: file\n"
            "    strategy: tree_sitter\n"
            "    language: csharp\n"
        )
        result = _compute(_make_repo(nodes, yaml_text))
        row = result["languages"].get("csharp", {})
        for flag in _TS_EVIDENCE:
            self.assertFalse(
                row.get(flag, False),
                f"csharp.{flag} must be False with no .cs files present",
            )

    def test_tree_sitter_multiple_wired_languages(self) -> None:
        """Two ``tree_sitter`` entries (go + rust) each credit only the
        language whose files are present."""
        nodes = {
            "file:pkg": {
                "type": "file",
                "props": {"file": "pkg/app.go", "source_strategy": "tree_sitter"},
            },
        }
        yaml_text = (
            "sources:\n"
            '  - glob: "**/*.go"\n'
            "    type: file\n"
            "    strategy: tree_sitter\n"
            "    language: go\n"
            '  - glob: "**/*.rs"\n'
            "    type: file\n"
            "    strategy: tree_sitter\n"
            "    language: rust\n"
        )
        result = _compute(_make_repo(nodes, yaml_text))
        self.assertTrue(result["languages"]["go"]["file"])
        # rust wired but no .rs file present -> stays False.
        self.assertFalse(result["languages"].get("rust", {}).get("file", False))


class MultiLanguageStrategyAttributionTest(unittest.TestCase):
    """(b) ``test_peer`` must only credit the language whose files exist."""

    def _python_only_repo(self) -> Path:
        nodes = {
            "file:app_test": {
                "type": "file",
                "props": {"file": "app_test.py"},
            },
        }
        yaml_text = (
            "sources:\n"
            '  - glob: "**/*_test.py"\n'
            "    type: file\n"
            "    strategy: test_peer\n"
        )
        return _make_repo(nodes, yaml_text)

    def test_python_only_repo_does_not_credit_other_languages(self) -> None:
        result = _compute(self._python_only_repo())
        # python gets its tests/file flags from test_peer...
        self.assertTrue(result["languages"]["python"]["tests"])
        self.assertTrue(result["languages"]["python"]["file"])
        # ...but the five languages with no files present must stay all-False.
        for lang in ("csharp", "go", "java", "rust", "typescript"):
            row = result["languages"].get(lang, {})
            self.assertFalse(
                row.get("tests", False),
                f"{lang}.tests must be False in a Python-only repo",
            )
            self.assertFalse(
                row.get("file", False),
                f"{lang}.file must be False in a Python-only repo",
            )

    def test_matching_language_still_credited(self) -> None:
        """A ``.cs`` test file present -> csharp (only) is credited."""
        nodes = {
            "file:FooTests": {
                "type": "file",
                "props": {"file": "tests/FooTests.cs"},
            },
        }
        yaml_text = (
            "sources:\n"
            '  - glob: "**/*Tests.cs"\n'
            "    type: file\n"
            "    strategy: test_peer\n"
        )
        result = _compute(_make_repo(nodes, yaml_text))
        self.assertTrue(result["languages"]["csharp"]["tests"])
        # A sibling language without files present must not ride along.
        self.assertFalse(
            result["languages"].get("python", {}).get("tests", False),
        )


if __name__ == "__main__":
    unittest.main()
