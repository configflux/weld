"""Finding-07 regression: ``wd init`` on a markdown repo outside docs/.

Field eval v0.23.1 Finding 07 (Medium): a docs repo whose markdown lives at
the root and under non-conventional directories (``adrs/``, ``architecture/``)
gets an empty ``sources:`` block and a zero-node graph -- silently. ``wd init``
printed "Found N files total" and wired nothing, with no signal that it
recognised nothing.

Two behaviours are pinned here:

1. When no conventional docs dir (``docs/`` / ``doc/`` / ``documentation/``) is
   found but markdown *is* present on disk, ``wd init`` wires a ``**/*.md``
   docs source so the ADRs and architecture notes become graph nodes. The
   evaluator confirmed this recovers the fixture docs repo (26 nodes / 45
   edges).

2. In every "recognised nothing" case -- no wired source entry at all, only
   commented-out stubs -- ``wd init`` says so on stderr instead of silently
   writing a wired-nothing config, conforming to ADR 0134's
   cannot-answer/explicit-reason principle.

The repro shape is taken verbatim from the bundle fixture
(docs/field-reports/weld-0.23.1-findings/fixture/make-fixture.sh, the
``docs-site`` child) and the transcript
(transcripts/07-init-misses-markdown-outside-docs.txt).
"""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path


from weld._yaml import parse_yaml  # noqa: E402
from weld.init import generate_yaml, init  # noqa: E402
from weld._init_framework_sources import (  # noqa: E402
    markdown_fallback_doc_source,
    yaml_has_wired_source,
)
from weld.strategies.markdown import extract as markdown_extract  # noqa: E402


def _make_docs_site(root: Path) -> None:
    """Recreate the bundle's ``docs-site`` child: markdown outside docs/."""
    (root / "adrs").mkdir(parents=True)
    (root / "architecture").mkdir(parents=True)
    (root / "README.md").write_text("# Platform Documentation\n", encoding="utf-8")
    (root / "platform-overview.md").write_text(
        "# Platform Overview\n", encoding="utf-8")
    (root / "adrs" / "0001-event-contracts.md").write_text(
        "# ADR 0001: Event Contracts\n", encoding="utf-8")
    (root / "adrs" / "0002-service-boundaries.md").write_text(
        "# ADR 0002: Service Boundaries\n", encoding="utf-8")
    (root / "architecture" / "data-flow.md").write_text(
        "# Data Flow\n", encoding="utf-8")


class MarkdownFallbackDocSourceTest(unittest.TestCase):
    """The pure fallback helper wires ``**/*.md`` only when it should."""

    def _md_files(self, root: Path) -> list[Path]:
        return list(root.rglob("*.md"))

    def test_wires_recursive_md_when_no_docs_dir(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_docs_site(root)
            entry = markdown_fallback_doc_source(
                self._md_files(root), root, doc_dirs=[])
            self.assertIsNotNone(entry)
            self.assertIn("**/*.md", entry)
            self.assertIn("markdown", entry)

    def test_fallback_entry_opts_readme_in(self) -> None:
        """N8: this entry fires because markdown *is* the content."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_docs_site(root)
            entry = markdown_fallback_doc_source(
                self._md_files(root), root, doc_dirs=[])
            self.assertIn("include_readme: true", entry)

    def test_no_fallback_when_docs_dir_present(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "docs").mkdir()
            (root / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
            entry = markdown_fallback_doc_source(
                self._md_files(root), root, doc_dirs=["docs"])
            self.assertIsNone(entry)

    def test_no_fallback_when_no_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "main.py").write_text("x = 1\n", encoding="utf-8")
            entry = markdown_fallback_doc_source(
                list(root.rglob("*.md")), root, doc_dirs=[])
            self.assertIsNone(entry)


class YamlHasWiredSourceTest(unittest.TestCase):
    """The recognised-nothing predicate distinguishes stubs from real entries."""

    def test_stub_only_yaml_has_no_wired_source(self) -> None:
        yaml_text = generate_yaml(
            languages={}, frameworks=[],
            dockerfiles=[], compose_files=[], ci_files=[],
            claude_agents=[], claude_commands=[],
            doc_dirs=[], python_globs=[], root_configs=[],
        )
        self.assertFalse(yaml_has_wired_source(yaml_text), yaml_text)

    def test_real_entry_yaml_has_wired_source(self) -> None:
        yaml_text = generate_yaml(
            languages={}, frameworks=[],
            dockerfiles=["Dockerfile"], compose_files=[], ci_files=[],
            claude_agents=[], claude_commands=[],
            doc_dirs=[], python_globs=[], root_configs=[],
        )
        self.assertTrue(yaml_has_wired_source(yaml_text), yaml_text)


class InitDocsRepoEndToEndTest(unittest.TestCase):
    """Finding 07: init on the docs-site shape wires markdown and says so."""

    def _run_init(self, root: Path) -> tuple[str, dict, str]:
        out = root / ".weld" / "discover.yaml"
        buf = io.StringIO()
        with redirect_stderr(buf):
            success = init(root, out, force=True)
        assert success
        text = out.read_text(encoding="utf-8")
        return text, parse_yaml(text), buf.getvalue()

    def test_docs_site_wires_recursive_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_docs_site(root)
            _text, data, _stderr = self._run_init(root)
            md_sources = [
                s for s in data.get("sources", [])
                if s.get("strategy") == "markdown"
            ]
            self.assertTrue(
                md_sources,
                "docs-site markdown repo must wire a markdown source",
            )
            self.assertTrue(
                any(s.get("glob") == "**/*.md" for s in md_sources),
                f"expected a **/*.md fallback glob, got {md_sources}",
            )

    def test_docs_site_sources_are_not_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_docs_site(root)
            text, _data, _stderr = self._run_init(root)
            self.assertTrue(
                yaml_has_wired_source(text),
                f"docs-site must not produce an all-stub config:\n{text}",
            )

    def test_recognised_nothing_repo_says_so(self) -> None:
        """A repo weld recognises nothing in gets an explicit stderr advisory."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # A file weld has no strategy for and no markdown: nothing wired.
            (root / "data.bin").write_text("nothing here\n", encoding="utf-8")
            text, _data, stderr = self._run_init(root)
            self.assertFalse(yaml_has_wired_source(text), text)
            self.assertIn("recognised nothing", stderr.lower())
            # The advisory tells you to hand-edit the config and re-run, so
            # the mode it names first must be the one that keeps that edit:
            # `--force` regenerates from scratch and would discard it.
            self.assertIn("wd init --refresh", stderr, stderr)
            self.assertLess(
                stderr.index("wd init --refresh"),
                stderr.index("wd init --force"),
                f"the destructive remedy is offered first: {stderr}",
            )

    def test_generated_fallback_reaches_the_readme(self) -> None:
        """N8: the config `wd init` writes must actually index README.md.

        The generator emitting the flag and the strategy honouring it are two
        halves that only matter joined, so the assertion runs the entry `wd
        init` wrote through the strategy that will read it -- the README is a
        node, it is listed in ``discovered_from``, and it is labelled by the
        title it declares rather than by the word "Readme", which is the term
        nobody searches for and the reason the finding's query answered with a
        different document.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_docs_site(root)
            _text, data, _stderr = self._run_init(root)
            entry = next(
                s for s in data["sources"]
                if s.get("strategy") == "markdown" and s.get("glob") == "**/*.md"
            )
            result = markdown_extract(root, entry, {})
            self.assertIn("doc:md/README", result.nodes, sorted(result.nodes))
            self.assertIn("README.md", result.discovered_from)
            self.assertEqual(
                result.nodes["doc:md/README"]["label"], "Platform Documentation",
            )

    def test_wired_repo_is_silent_about_recognising_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_docs_site(root)
            _text, _data, stderr = self._run_init(root)
            self.assertNotIn("recognised nothing", stderr.lower())


if __name__ == "__main__":
    unittest.main()
