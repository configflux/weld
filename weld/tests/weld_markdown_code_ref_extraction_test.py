"""Tests for doc -> code citation extraction in the markdown strategy.

Asserts that a backtick-quoted ``.py`` path or dotted-module reference in a
markdown body produces a ``documents`` edge from the citing ``doc:*`` node
to the cited ``file:*`` node -- the fix for bd ziv1 ("ADRs that govern a
code module are not reachable from that module"). See ADR 0128 for the
vocabulary decision (reuse ``documents``, not a new edge type) and the
match rule (explicit textual citation resolved against the real
filesystem, never a fuzzy/thematic match).

Sibling of ``weld_markdown_link_extraction_test.py`` (the doc->doc pass);
same strategy, same ``extract()`` entry point, a second edge kind.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.strategies.markdown import extract

_SOURCE = {
    "glob": "docs/*.md",
    "id_prefix": "doc:docs",
    "doc_kind": "guide",
}


def _setup(d: str, docs: dict, code: dict | None = None) -> Path:
    """Write markdown under ``d/docs/`` and code files under ``d/``."""
    root = Path(d)
    docs_dir = root / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    for rel, body in docs.items():
        path = docs_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    for rel, body in (code or {}).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


def _documents_edges(result):
    return [e for e in result.edges if e["type"] == "documents"]


class PathCitationTest(unittest.TestCase):
    """A backtick-quoted ``.py`` path resolves to a ``documents`` edge."""

    def test_bare_py_path_emits_documents_edge(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _setup(
                d,
                {"a.md": "See `pkg/thing.py` for details.\n"},
                {"pkg/thing.py": "# real module\n"},
            )
            edges = _documents_edges(extract(root, _SOURCE, {}))
            self.assertEqual(
                [(e["from"], e["to"]) for e in edges],
                [("doc:docs/a", "file:pkg/thing")],
            )

    def test_symbol_suffix_still_resolves_to_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _setup(
                d,
                {"a.md": "See `pkg/thing.py:some_func` for details.\n"},
                {"pkg/thing.py": "def some_func(): ...\n"},
            )
            edges = _documents_edges(extract(root, _SOURCE, {}))
            self.assertEqual(
                [(e["from"], e["to"]) for e in edges],
                [("doc:docs/a", "file:pkg/thing")],
            )

    def test_missing_path_no_edge(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _setup(d, {"a.md": "See `pkg/does_not_exist.py`.\n"})
            edges = _documents_edges(extract(root, _SOURCE, {}))
            self.assertEqual(edges, [])

    def test_traversal_path_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _setup(
                d,
                {"a.md": "See `../outside/thing.py`.\n"},
                {"pkg/thing.py": "# decoy\n"},
            )
            (root.parent / "outside").mkdir(exist_ok=True)
            (root.parent / "outside" / "thing.py").write_text("x", encoding="utf-8")
            edges = _documents_edges(extract(root, _SOURCE, {}))
            self.assertEqual(edges, [])


class DottedCitationTest(unittest.TestCase):
    """A backtick-quoted dotted module/symbol reference resolves by the
    longest real prefix, floored at two segments (ADR 0128 §4)."""

    def test_module_plus_symbol_resolves_the_module_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _setup(
                d,
                {"a.md": "See `pkg.thing.some_symbol` for details.\n"},
                {"pkg/thing.py": "def some_symbol(): ...\n"},
            )
            edges = _documents_edges(extract(root, _SOURCE, {}))
            self.assertEqual(
                [(e["from"], e["to"]) for e in edges],
                [("doc:docs/a", "file:pkg/thing")],
            )

    def test_bare_two_segment_module_resolves(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _setup(
                d,
                {"a.md": "See `pkg.thing` for details.\n"},
                {"pkg/thing.py": "# module\n"},
            )
            edges = _documents_edges(extract(root, _SOURCE, {}))
            self.assertEqual(
                [(e["from"], e["to"]) for e in edges],
                [("doc:docs/a", "file:pkg/thing")],
            )

    def test_package_init_resolves(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _setup(
                d,
                {"a.md": "See `pkg.sub` for details.\n"},
                {"pkg/sub/__init__.py": "# package\n"},
            )
            edges = _documents_edges(extract(root, _SOURCE, {}))
            self.assertEqual(
                [(e["from"], e["to"]) for e in edges],
                [("doc:docs/a", "file:pkg/sub/__init__")],
            )

    def test_single_segment_prefix_never_resolves(self) -> None:
        """The precision guard: a real top-level package must not become a
        fallback match for an unrelated, made-up submodule name."""
        with tempfile.TemporaryDirectory() as d:
            root = _setup(
                d,
                {"a.md": "See `pkg.totally_made_up_thing` for details.\n"},
                {"pkg/__init__.py": "# real top-level package\n"},
            )
            edges = _documents_edges(extract(root, _SOURCE, {}))
            self.assertEqual(
                edges, [],
                "a made-up submodule must not fall back to the real "
                "top-level package's __init__.py",
            )

    def test_single_word_no_edge(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _setup(
                d,
                {"a.md": "See `something` for details.\n"},
                {"something.py": "# would resolve if bare words counted\n"},
            )
            edges = _documents_edges(extract(root, _SOURCE, {}))
            self.assertEqual(edges, [])

    def test_unresolvable_dotted_reference_no_edge(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _setup(d, {"a.md": "See `pkg.nope.also_nope` for details.\n"})
            edges = _documents_edges(extract(root, _SOURCE, {}))
            self.assertEqual(edges, [])


class FencedCitationExcludedTest(unittest.TestCase):
    """A citation that only ever renders as code is not a reference."""

    def test_citation_inside_a_fenced_block_emits_no_edge(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _setup(
                d,
                {"a.md": "Example:\n\n```python\n# see pkg/thing.py\n`pkg/thing.py`\n```\n"},
                {"pkg/thing.py": "# module\n"},
            )
            edges = _documents_edges(extract(root, _SOURCE, {}))
            self.assertEqual(edges, [])

    def test_real_citation_beside_a_sampled_one_still_emits_its_edge(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _setup(
                d,
                {
                    "a.md": (
                        "See `pkg/thing.py` for the real module.\n\n"
                        "```python\n`pkg/other.py`\n```\n"
                    ),
                },
                {"pkg/thing.py": "# real\n", "pkg/other.py": "# sampled only\n"},
            )
            edges = _documents_edges(extract(root, _SOURCE, {}))
            self.assertEqual(
                [(e["from"], e["to"]) for e in edges],
                [("doc:docs/a", "file:pkg/thing")],
            )


class DedupAndDeterminismTest(unittest.TestCase):
    def test_duplicate_citations_deduped(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _setup(
                d,
                {"a.md": "`pkg/thing.py` ... and again `pkg/thing.py`.\n"},
                {"pkg/thing.py": "# module\n"},
            )
            edges = _documents_edges(extract(root, _SOURCE, {}))
            self.assertEqual(len(edges), 1, f"expected one edge, got {edges}")

    def test_edges_sorted_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _setup(
                d,
                {"a.md": "`pkg/c.py`, `pkg/a.py`, `pkg/b.py`.\n"},
                {"pkg/a.py": "# a\n", "pkg/b.py": "# b\n", "pkg/c.py": "# c\n"},
            )
            edges = _documents_edges(extract(root, _SOURCE, {}))
            tos = [e["to"] for e in edges]
            self.assertEqual(tos, sorted(tos), f"edges not sorted: {tos}")


class CitationProvenanceTest(unittest.TestCase):
    """Every documents edge names the doc whose body held the citation."""

    def test_provenance_is_the_mentioning_doc(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = _setup(
                d,
                {"a.md": "See `pkg/thing.py`.\n"},
                {"pkg/thing.py": "# module\n"},
            )
            edges = _documents_edges(extract(root, _SOURCE, {}))
            self.assertEqual(len(edges), 1)
            self.assertEqual(
                edges[0]["props"].get("provenance"), {"file": "docs/a.md"},
            )
            self.assertEqual(edges[0]["props"].get("source_strategy"), "markdown")
            self.assertEqual(edges[0]["props"].get("confidence"), "inferred")
            self.assertEqual(edges[0]["props"].get("authority"), "derived")


class EdgeContractValidationTest(unittest.TestCase):
    """Emitted documents edges pass the contract validator."""

    def test_emitted_edges_pass_validation(self) -> None:
        from weld.contract import validate_edge

        with tempfile.TemporaryDirectory() as d:
            root = _setup(
                d,
                {"a.md": "See `pkg/thing.py:some_func` and `pkg.other`.\n"},
                {"pkg/thing.py": "def some_func(): ...\n", "pkg/other.py": "# x\n"},
            )
            result = extract(root, _SOURCE, {})
            node_ids = set(result.nodes.keys()) | {
                e["to"] for e in _documents_edges(result)
            }
            for edge in _documents_edges(result):
                errors = validate_edge(edge, node_ids)
                self.assertEqual(errors, [], f"edge {edge}: {errors}")


if __name__ == "__main__":
    unittest.main()
