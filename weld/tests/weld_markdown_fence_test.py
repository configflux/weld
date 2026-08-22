"""Regression: fenced code blocks must not contribute document structure.

Before bd ve41 every markdown scan in ``weld/strategies`` walked
``text.splitlines()`` with no fence state, so a ``## Added`` that exists only
inside a ```` ``` ```` sample minted a section node, a span covering the
sample, and a ``props.headings`` query token. This repository had 83 such
lines across the globs it indexes, including ``docs/release.md``'s changelog
template.

The tests are layered at the seams the defect can reappear at: the shared
scanner's own rules, then each of the three callers that consume it
(``_collect_heading_texts``, ``_parse_sections``, ``runbook._extract_title``),
so a caller that stops using the scanner fails here rather than passing on
the scanner's unit tests alone.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.strategies._markdown_fence import (
    content_lines,
    content_text,
    iter_headings,
)
from weld.strategies.markdown import (
    _collect_heading_texts,
    _extract_md_link_targets,
    _parse_sections,
    extract,
)
from weld.strategies.runbook import _extract_title


def _texts(text: str) -> list[str]:
    """Return only the line text yielded by the scanner, dropping indices."""

    return [line for _index, line in content_lines(text)]


class FenceScannerTest(unittest.TestCase):
    """The shared scanner's block-resolution rules."""

    def test_body_and_delimiters_are_withheld(self) -> None:
        text = "before\n```\ninside\n```\nafter\n"
        self.assertEqual(_texts(text), ["before", "after"])

    def test_index_stays_a_document_line_number(self) -> None:
        """Callers report line numbers, so indices must not be re-packed."""

        text = "before\n```\ninside\n```\nafter\n"
        self.assertEqual(
            list(content_lines(text)), [(0, "before"), (4, "after")]
        )

    def test_indented_fence_is_recognized(self) -> None:
        """A fence nested in a list item is the common real-world shape.

        A column-anchored fence matches none of them, which is exactly how
        the sibling scanner in ``tools/`` came to match empty text.
        """

        text = "1. Step:\n\n   ```bash\n   # not a heading\n   ```\n\n## Real\n"
        self.assertNotIn("   # not a heading", _texts(text))
        self.assertIn("## Real", _texts(text))

    def test_tilde_fence_is_recognized(self) -> None:
        text = "~~~\n## Fenced\n~~~\n## Real\n"
        self.assertEqual(_texts(text), ["## Real"])

    def test_tilde_does_not_close_a_backtick_fence(self) -> None:
        text = "```\n~~~\n## Fenced\n```\n## Real\n"
        self.assertEqual(_texts(text), ["## Real"])

    def test_shorter_run_does_not_close_a_longer_fence(self) -> None:
        """A doc that *shows* a fence nests one inside a longer one."""

        text = "````\n```\n## Fenced\n```\n````\n## Real\n"
        self.assertEqual(_texts(text), ["## Real"])

    def test_longer_run_closes_a_shorter_fence(self) -> None:
        text = "```\n## Fenced\n`````\n## Real\n"
        self.assertEqual(_texts(text), ["## Real"])

    def test_closer_with_info_string_does_not_close(self) -> None:
        text = "```\n## Fenced\n```python\n## Still fenced\n```\n## Real\n"
        self.assertEqual(_texts(text), ["## Real"])

    def test_unclosed_fence_runs_to_end_of_document(self) -> None:
        """CommonMark: an unclosed block ends with the document."""

        text = "## Real\n```\n## Fenced\nmore\n"
        self.assertEqual(_texts(text), ["## Real"])

    def test_backtick_info_string_with_backtick_opens_nothing(self) -> None:
        """``` ```a` b ``` is inline code, not an opener (CommonMark 4.5).

        Promoting it would swallow every heading below it to EOF -- trading
        an over-reporting bug for a silent under-reporting one.
        """

        text = "```a`b\n## Real\n"
        self.assertIn("## Real", _texts(text))

    def test_short_run_is_not_a_fence(self) -> None:
        text = "``\n## Real\n"
        self.assertIn("## Real", _texts(text))

    def test_fence_free_document_is_passed_through(self) -> None:
        text = "# Title\n\n## One\n\ntext\n"
        self.assertEqual(_texts(text), ["# Title", "", "## One", "", "text"])


class HeadingWalkTest(unittest.TestCase):
    """The shared ATX walk the three callers replaced their own copies with.

    These pin the parity the refactor had to preserve; each case is a shape
    the old per-caller ``startswith`` tests already agreed on.
    """

    def test_level_and_index_are_reported(self) -> None:
        text = "# One\n\n## Two\n\n### Three\n"
        self.assertEqual(
            list(iter_headings(text)),
            [(0, 1, "One"), (2, 2, "Two"), (4, 3, "Three")],
        )

    def test_hash_run_without_a_space_is_not_a_heading(self) -> None:
        self.assertEqual(list(iter_headings("##Two\n#One\n")), [])

    def test_empty_atx_heading_is_not_a_heading(self) -> None:
        """CommonMark allows ``##`` and ``## `` as empty headings; we do not.

        Parity with the ``startswith("## ")`` tests this walk replaced, which
        rejected both because the line is stripped before it is matched. An
        empty heading would otherwise mint a section node with an empty slug.
        """

        self.assertEqual(list(iter_headings("##\n")), [])
        self.assertEqual(list(iter_headings("## \n")), [])
        self.assertEqual(_parse_sections("## \n"), [])

    def test_seventh_hash_run_is_not_a_heading(self) -> None:
        self.assertEqual(list(iter_headings("####### Deep\n")), [])

    def test_h4_is_walked_but_not_collected(self) -> None:
        self.assertEqual(list(iter_headings("#### Four\n")), [(0, 4, "Four")])
        self.assertEqual(_collect_heading_texts("#### Four\n"), [])

    def test_fenced_headings_are_absent_from_the_walk(self) -> None:
        self.assertEqual(
            list(iter_headings("```\n## Fenced\n```\n## Real\n")), [(3, 2, "Real")]
        )


_TEMPLATE_DOC = """\
# Release

## Before you start

Check the tag.

## Generate the changelog

Paste this into CHANGELOG.md:

```markdown
## vX.Y.Z - YYYY-MM-DD

### Added

- new thing

### Changed

- changed thing
```

## Rollback and recovery

Revert the tag.
"""


class HeadingCollectionTest(unittest.TestCase):
    """``props.headings`` must carry only headings the document renders."""

    def test_template_headings_are_not_collected(self) -> None:
        headings = _collect_heading_texts(_TEMPLATE_DOC)
        for phantom in ("vX.Y.Z - YYYY-MM-DD", "Added", "Changed"):
            self.assertNotIn(phantom, headings)

    def test_real_headings_around_the_fence_survive(self) -> None:
        headings = _collect_heading_texts(_TEMPLATE_DOC)
        self.assertEqual(
            headings,
            ["Before you start", "Generate the changelog", "Rollback and recovery"],
        )


class SectionParsingTest(unittest.TestCase):
    """Section nodes and their spans must ignore fenced samples."""

    def test_fenced_heading_mints_no_section(self) -> None:
        headings = [section["heading"] for section in _parse_sections(_TEMPLATE_DOC)]
        self.assertEqual(
            headings,
            ["Before you start", "Generate the changelog", "Rollback and recovery"],
        )

    def test_span_covers_the_sample_it_encloses(self) -> None:
        """The fence belongs to the section that contains it.

        This is the half a heading-only filter would get wrong: dropping the
        phantom heading while still ending the enclosing section at its line
        would leave a section that stops mid-sample.
        """

        sections = {s["heading"]: s for s in _parse_sections(_TEMPLATE_DOC)}
        changelog = sections["Generate the changelog"]
        rollback = sections["Rollback and recovery"]
        self.assertEqual(changelog["end_line"], rollback["start_line"] - 1)
        fence_line = _TEMPLATE_DOC.splitlines().index("```markdown") + 1
        self.assertLess(changelog["start_line"], fence_line)
        self.assertGreater(changelog["end_line"], fence_line)

    def test_last_section_still_ends_at_document_end(self) -> None:
        sections = _parse_sections(_TEMPLATE_DOC)
        self.assertEqual(
            sections[-1]["end_line"], len(_TEMPLATE_DOC.splitlines())
        )


class RunbookTitleTest(unittest.TestCase):
    """The runbook title scan shares the defect and the fix."""

    def test_shell_comment_in_a_leading_fence_is_not_the_title(self) -> None:
        text = "```bash\n# restart the worker\n```\n\n# Worker Restart\n"
        self.assertEqual(_extract_title(text), "Worker Restart")

    def test_real_h1_is_still_extracted(self) -> None:
        self.assertEqual(_extract_title("# Worker Restart\n\ntext\n"), "Worker Restart")


class MarkdownStrategyEndToEndTest(unittest.TestCase):
    """The whole strategy, from a file on disk to emitted nodes."""

    def test_doc_node_and_sections_skip_the_fence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            (root / "docs" / "release.md").write_text(
                _TEMPLATE_DOC, encoding="utf-8"
            )
            result = extract(
                root,
                {
                    "glob": "docs/*.md",
                    "id_prefix": "doc:docs",
                    "extract_sections": True,
                },
                {},
            )

        doc = result.nodes["doc:docs/release"]
        self.assertNotIn("Added", doc["props"]["headings"])
        self.assertIn("Rollback and recovery", doc["props"]["headings"])
        section_ids = sorted(nid for nid in result.nodes if "#" in nid)
        self.assertEqual(
            section_ids,
            [
                "doc:docs/release#before-you-start",
                "doc:docs/release#generate-the-changelog",
                "doc:docs/release#rollback-and-recovery",
            ],
        )
        contains = [e["to"] for e in result.edges if e["type"] == "contains"]
        self.assertEqual(sorted(contains), section_ids)


class ContentTextTest(unittest.TestCase):
    """The non-line-oriented view of the same scan (bd w624)."""

    def test_fenced_block_is_removed(self) -> None:
        text = "before\n```\ninside\n```\nafter\n"
        self.assertEqual("before\nafter", content_text(text))

    def test_prose_is_joined_so_a_wrapped_construct_survives(self) -> None:
        # The reason this is not a per-line scan: markdown allows a link
        # label to wrap, and a per-line regex would silently stop seeing it.
        # Trading an over-report for an under-report is the worse direction.
        self.assertEqual("a\nb", content_text("a\nb\n"))

    def test_unclosed_fence_runs_to_end_of_document(self) -> None:
        self.assertEqual("before", content_text("before\n```\ntrailing\n"))


class LinkExtractionSkipsFencesTest(unittest.TestCase):
    """The caller bd w624 added: inter-doc link targets (bd w624)."""

    def test_link_inside_a_fence_is_not_a_target(self) -> None:
        text = "Real [a](a.md).\n\n```markdown\n[sample](sample.md)\n```\n"
        self.assertEqual([("a.md", "")], _extract_md_link_targets(text))

    def test_link_in_prose_is_still_a_target(self) -> None:
        self.assertEqual(
            [("guide.md", "#anchor")],
            _extract_md_link_targets("See [g](guide.md#anchor).\n"),
        )

    def test_wrapped_label_still_matches(self) -> None:
        # Would be lost by a per-line scan; kept by joining the prose.
        self.assertEqual(
            [("guide.md", "")],
            _extract_md_link_targets("See [the\nguide](guide.md).\n"),
        )

    def test_tilde_fence_and_long_backtick_fence_are_both_honoured(self) -> None:
        text = (
            "~~~\n[t](t.md)\n~~~\n"
            "````\n[inner](inner.md)\n```\n[still-inside](still.md)\n````\n"
            "[real](real.md)\n"
        )
        self.assertEqual([("real.md", "")], _extract_md_link_targets(text))


if __name__ == "__main__":
    unittest.main()
