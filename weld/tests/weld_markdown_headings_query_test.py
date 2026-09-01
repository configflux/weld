"""Regression: doc nodes must surface H2/H3 headings to ``wd query``.

Closes the 2026-05-15 dogfood gap where ``wd query "language support"``
returned ``file:weld/_discover_federate_origin`` instead of any of the
README / launch / weld-README nodes that actually own the language-support
claims.

The fix has three load-bearing layers; each gets a unit test here so the
regression is held at the seam where it can fail (strategy emission /
inverted-index population / runtime match surface) plus one end-to-end
test that exercises the whole chain through ``Graph.query``.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


from weld.graph import Graph  # noqa: E402
from weld.query_index import build_index, node_tokens  # noqa: E402
from weld.strategies.markdown import extract  # noqa: E402

_LANG_DOC = """\
# Bundled Languages and Support

Brief overview, no language-support phrase here.

## Language support

Python, TypeScript, Go, Rust, C#, C++, and Java ship as built-in
strategies.

## Trust model

Local-first. No network calls unless opt-in.
"""

_README_DOC = """\
# Weld

## Supported languages

Built-in tree-sitter strategies cover Python, TypeScript, Go, Rust,
C#, C++, Java.
"""


def _write_doc(root: Path, rel: str, text: str) -> Path:
    """Helper: materialize *text* at *root / rel* and ensure parents exist."""
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    return target


class HeadingsExtractionTest(unittest.TestCase):
    """The markdown strategy emits ``props.headings`` per doc node."""

    def _extract(self, text: str, **overrides) -> dict:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write_doc(root, "docs/guide.md", text)
            source = {"glob": "docs/*.md", "id_prefix": "doc:guide", **overrides}
            result = extract(root, source, {})
            return result.nodes

    def test_doc_node_has_headings_prop(self) -> None:
        """A doc with H2 and H3 headings carries them on ``props.headings``."""
        nodes = self._extract(_LANG_DOC)
        # The strategy uses ``{id_prefix}/{md.stem}``; with id_prefix
        # "doc:guide" the doc node id is "doc:guide/guide".
        doc = nodes["doc:guide/guide"]
        headings = doc["props"].get("headings")
        self.assertIsNotNone(headings, "doc node must carry props.headings")
        self.assertIn("Language support", headings)
        self.assertIn("Trust model", headings)

    def test_headings_sorted_and_deduped(self) -> None:
        """``props.headings`` is deterministic: sorted, deduped."""
        text = "# Title\n\n## A heading\n\n## A heading\n\n## B heading\n"
        nodes = self._extract(text)
        headings = nodes["doc:guide/guide"]["props"]["headings"]
        self.assertEqual(headings, ["A heading", "B heading"])

    def test_no_headings_when_doc_has_none(self) -> None:
        """A heading-less doc must not synthesise a headings prop."""
        nodes = self._extract("Plain text only, no headings.\n")
        doc = nodes["doc:guide/guide"]
        # Either absent or an explicit empty list; both keep the inverted
        # index from indexing empty strings.
        self.assertFalse(doc["props"].get("headings"))


class IncludeReadmeFlagTest(unittest.TestCase):
    """``include_readme`` opts the README.md skip out."""

    def test_default_skips_readme(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write_doc(root, "docs/README.md", _README_DOC)
            _write_doc(root, "docs/launch.md", _LANG_DOC)
            result = extract(
                root,
                {"glob": "docs/*.md", "id_prefix": "doc:docs"},
                {},
            )
            self.assertNotIn("doc:docs/README", result.nodes)
            self.assertIn("doc:docs/launch", result.nodes)

    def test_include_readme_emits_readme_doc(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write_doc(root, "README.md", _README_DOC)
            result = extract(
                root,
                {
                    "glob": "*.md",
                    "id_prefix": "doc:root",
                    "include_readme": True,
                },
                {},
            )
            self.assertIn("doc:root/README", result.nodes)
            doc = result.nodes["doc:root/README"]
            self.assertIn(
                "Supported languages",
                doc["props"].get("headings", []),
            )


class ReadmeLabelTest(unittest.TestCase):
    """A README doc node is labelled by its title, not by "Readme".

    Every other doc's filename restates its title, so the stem is the label
    and the H1 is redundant. ``README.md`` is the exception: the name is a
    placement convention that describes nothing, so a node labelled "Readme"
    could not be reached by the one term a reader would search for -- a docs
    repository whose README declares ``# Platform Documentation`` answered
    that query with a different document (field eval v0.24.0 N8).
    """

    def _label(self, rel: str, text: str, **source: object) -> str:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write_doc(root, rel, text)
            result = extract(
                root,
                {"glob": "*.md", "id_prefix": "doc:root", **source},
                {},
            )
            (node,) = result.nodes.values()
            return node["label"]

    def test_readme_takes_its_h1_as_the_label(self) -> None:
        label = self._label(
            "README.md",
            "# Platform Documentation\n\nIndex of everything.\n",
            include_readme=True,
        )
        self.assertEqual(label, "Platform Documentation")

    def test_readme_without_a_title_falls_back_to_the_stem(self) -> None:
        label = self._label(
            "README.md", "No heading at all.\n", include_readme=True,
        )
        self.assertEqual(label, "Readme")

    def test_readme_title_inside_a_fence_is_not_the_label(self) -> None:
        """The fence-blind scan this replaced would title it "# Not a title"."""
        label = self._label(
            "README.md",
            "```sh\n# Not a title\n```\n\n# Platform Documentation\n",
            include_readme=True,
        )
        self.assertEqual(label, "Platform Documentation")

    def test_other_docs_keep_their_filename_label(self) -> None:
        """The rule is README-only; widening it would relabel every doc node."""
        label = self._label(
            "platform-overview.md", "# A Completely Different Title\n",
        )
        self.assertEqual(label, "Platform Overview")


class HeadingsIndexedForQueryTest(unittest.TestCase):
    """``props.headings`` must reach the inverted index and match surface."""

    def _doc_node(self, headings: list[str]) -> dict:
        return {
            "type": "doc",
            "label": "Launch",
            "props": {
                "file": "docs/launch.md",
                "doc_kind": "guide",
                "source_strategy": "markdown",
                "authority": "derived",
                "confidence": "definite",
                "roles": ["doc"],
                "headings": headings,
            },
        }

    def test_node_tokens_surfaces_heading_words(self) -> None:
        """A heading like ``Language support`` must produce both tokens."""
        node = self._doc_node(["Language support", "Trust model"])
        tokens = set(node_tokens("doc:docs/launch", node))
        self.assertIn("language", tokens)
        self.assertIn("support", tokens)
        self.assertIn("trust", tokens)

    def test_build_index_finds_doc_by_heading(self) -> None:
        node = self._doc_node(["Language support"])
        nodes = {"doc:docs/launch": node}
        index = build_index(nodes)
        self.assertIn("language", index)
        self.assertIn("doc:docs/launch", index["language"])
        self.assertIn("support", index)

    def test_non_string_headings_skipped(self) -> None:
        """Defensive: a poisoned graph with non-string entries must not raise."""
        node = self._doc_node([])
        node["props"]["headings"] = ["Good heading", 42, None, "Also good"]
        tokens = set(node_tokens("doc:docs/launch", node))
        self.assertIn("good", tokens)
        self.assertIn("also", tokens)

    def test_match_token_groups_hits_headings(self) -> None:
        """Graph._match_token_groups must consult ``props.headings``."""
        node = self._doc_node(["Language support"])
        # Use the same token-group shape ``Graph.query`` builds.
        groups = [["language"], ["support"]]
        hits = Graph._match_token_groups(groups, "doc:docs/launch", node)
        self.assertEqual(hits, 2)


class EndToEndQueryTest(unittest.TestCase):
    """``Graph.query('language support')`` returns the doc node end-to-end."""

    def test_query_surfaces_doc_with_heading_match(self) -> None:
        # Build a minimal graph payload with a single doc node whose only
        # ``language``/``support`` signal is in ``props.headings``. If any
        # layer in the chain (strategy -> indexing -> matching) drops the
        # field, this end-to-end test fails.
        nodes = {
            "doc:docs/launch": {
                "type": "doc",
                "label": "Launch",
                "props": {
                    "file": "docs/launch.md",
                    "doc_kind": "guide",
                    "source_strategy": "markdown",
                    "authority": "derived",
                    "confidence": "definite",
                    "roles": ["doc"],
                    "headings": ["Language support", "Trust model"],
                },
            },
        }
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".weld").mkdir()
            (root / ".weld" / "graph.json").write_text(json.dumps({
                "meta": {"version": 1, "updated_at": "2026-05-15T00:00:00Z"},
                "nodes": nodes,
                "edges": [],
            }))
            graph = Graph(root)
            graph.load()
            result = graph.query("language support")
            ids = [m["id"] for m in result["matches"]]
            self.assertIn("doc:docs/launch", ids, f"got matches: {ids}")


if __name__ == "__main__":
    unittest.main()
