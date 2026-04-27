"""Tests for managed-region markers in `wd bootstrap` (ADR 0033).

Covers the marker parser, the region-aware writer (no-op, refuse, clobber,
append), the ``--diff`` regional comparison, the ``--include-unmanaged``
escape hatch, and the template-authoring lint (every bundled template parses
cleanly; sibling ``.cli.md`` variants declare the same region names; the
federation paragraph parses as a single region named ``federation``).
"""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from weld.bootstrap import _FEDERATION_PARAGRAPH, bootstrap
from weld.bootstrap_managed import (
    MarkerError,
    Region,
    append_region,
    has_any_start,
    lint_template_dir,
    parse_regions,
    region_diff,
    replace_region_bodies,
)
from weld.cli import main as cli_main

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


class ParserPositiveTest(unittest.TestCase):
    """Well-formed marker pairs parse into Region objects."""

    def test_single_region(self) -> None:
        text = (
            "intro\n"
            "<!-- weld-managed:start name=foo -->\n"
            "body line one\n"
            "body line two\n"
            "<!-- weld-managed:end name=foo -->\n"
            "outro\n"
        )
        regions = parse_regions(text)
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0].name, "foo")
        self.assertEqual(
            regions[0].body, "body line one\nbody line two\n",
        )
        self.assertIsInstance(regions[0], Region)

    def test_two_regions_same_file(self) -> None:
        text = (
            "<!-- weld-managed:start name=alpha -->\nA\n<!-- weld-managed:end name=alpha -->\n"
            "between\n"
            "<!-- weld-managed:start name=beta -->\nB\n<!-- weld-managed:end name=beta -->\n"
        )
        names = [r.name for r in parse_regions(text)]
        self.assertEqual(names, ["alpha", "beta"])

    def test_hash_marker_variant(self) -> None:
        text = (
            "[head]\n"
            "# weld-managed:start name=mcp\n"
            "command = \"x\"\n"
            "# weld-managed:end name=mcp\n"
        )
        regions = parse_regions(text)
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0].name, "mcp")
        self.assertIn("command", regions[0].body)

    def test_empty_region_body(self) -> None:
        text = (
            "<!-- weld-managed:start name=blank -->\n"
            "<!-- weld-managed:end name=blank -->\n"
        )
        regions = parse_regions(text)
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0].body, "")


class ParserNegativeTest(unittest.TestCase):
    """Malformed marker pairs are rejected with a clear error."""

    def test_missing_name_attribute(self) -> None:
        with self.assertRaises(MarkerError) as cm:
            parse_regions("<!-- weld-managed:start -->\nbody\n<!-- weld-managed:end -->\n")
        self.assertIn("malformed", str(cm.exception).lower())

    def test_mismatched_end_name(self) -> None:
        with self.assertRaises(MarkerError):
            parse_regions(
                "<!-- weld-managed:start name=foo -->\nx\n"
                "<!-- weld-managed:end name=bar -->\n"
            )

    def test_nested_start(self) -> None:
        with self.assertRaises(MarkerError) as cm:
            parse_regions(
                "<!-- weld-managed:start name=outer -->\n"
                "<!-- weld-managed:start name=inner -->\nx\n"
                "<!-- weld-managed:end name=inner -->\n"
                "<!-- weld-managed:end name=outer -->\n"
            )
        self.assertIn("nested", str(cm.exception).lower())

    def test_duplicate_region_names(self) -> None:
        with self.assertRaises(MarkerError) as cm:
            parse_regions(
                "<!-- weld-managed:start name=dup -->\nA\n<!-- weld-managed:end name=dup -->\n"
                "<!-- weld-managed:start name=dup -->\nB\n<!-- weld-managed:end name=dup -->\n"
            )
        self.assertIn("duplicate", str(cm.exception).lower())

    def test_unterminated_region(self) -> None:
        with self.assertRaises(MarkerError):
            parse_regions("<!-- weld-managed:start name=open -->\nbody\n")

    def test_dangling_end(self) -> None:
        with self.assertRaises(MarkerError):
            parse_regions("<!-- weld-managed:end name=stray -->\n")


class HasAnyStartTest(unittest.TestCase):
    def test_detects_start(self) -> None:
        self.assertTrue(has_any_start("<!-- weld-managed:start name=foo -->\n"))

    def test_no_start_in_unmarked_text(self) -> None:
        self.assertFalse(has_any_start("this file has no markers at all\njust prose\n"))

    def test_end_only_does_not_count_as_start(self) -> None:
        self.assertFalse(has_any_start("<!-- weld-managed:end name=stray -->\n"))


class RegionDiffTest(unittest.TestCase):
    def _wrap(self, body: str, name: str = "r") -> str:
        return (
            f"<!-- weld-managed:start name={name} -->\n"
            f"{body}"
            f"<!-- weld-managed:end name={name} -->\n"
        )

    def test_identical_regions_yield_empty_diff(self) -> None:
        text = self._wrap("hello\n")
        diff_text, drifted, missing = region_diff(text, text, fromfile="a", tofile="b")
        self.assertEqual((diff_text, drifted, missing), ("", [], []))

    def test_drifted_region_emits_unified_diff(self) -> None:
        existing = self._wrap("user-edit\n")
        template = self._wrap("template-version\n")
        diff_text, drifted, missing = region_diff(existing, template, fromfile="a", tofile="b")
        self.assertEqual(drifted, ["r"])
        self.assertEqual(missing, [])
        self.assertIn("-user-edit", diff_text)
        self.assertIn("+template-version", diff_text)
        self.assertIn("[region=r]", diff_text)

    def test_missing_region_in_existing(self) -> None:
        existing = self._wrap("placeholder\n", name="other")
        template = self._wrap("placeholder\n", name="other") + self._wrap("new\n", name="extra")
        _, drifted, missing = region_diff(existing, template, fromfile="a", tofile="b")
        self.assertEqual(missing, ["extra"])
        self.assertEqual(drifted, [])


class WriterMissingDestTest(unittest.TestCase):
    """Missing destination -> bootstrap writes the entire template verbatim."""

    def test_write_seeds_full_template_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                bootstrap("copilot", root)
            skill = root / ".github" / "skills" / "weld" / "SKILL.md"
            self.assertTrue(skill.is_file())
            content = skill.read_text(encoding="utf-8")
            # Markers must be in the freshly seeded file.
            self.assertIn("<!-- weld-managed:start name=retrieval-commands", content)


class WriterRegionSemanticsTest(unittest.TestCase):
    """Writer no-op / refuse / clobber / append paths."""

    def _seed_copilot(self, root: Path) -> Path:
        bootstrap("copilot", root, force=True)
        return root / ".github" / "skills" / "weld" / "SKILL.md"

    def test_matching_region_body_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            skill = self._seed_copilot(root)
            before = skill.read_text(encoding="utf-8")
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                bootstrap("copilot", root)
            self.assertEqual(skill.read_text(encoding="utf-8"), before)
            self.assertIn("up-to-date", buf.getvalue().lower())

    def test_differing_region_refuses_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            skill = self._seed_copilot(root)
            text = skill.read_text(encoding="utf-8")
            text = text.replace(
                "Default starting point",
                "OPERATOR HAND-EDIT",
            )
            skill.write_text(text, encoding="utf-8")
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                bootstrap("copilot", root)
            output = buf.getvalue()
            # File must NOT be clobbered.
            self.assertIn("OPERATOR HAND-EDIT", skill.read_text(encoding="utf-8"))
            # Output must name the drifted region and point at --force/--diff.
            self.assertIn("retrieval-commands", output)
            self.assertIn("--force", output)

    def test_force_clobbers_drifted_region_and_logs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            skill = self._seed_copilot(root)
            text = skill.read_text(encoding="utf-8")
            text = text.replace(
                "Default starting point",
                "OPERATOR HAND-EDIT",
            )
            skill.write_text(text, encoding="utf-8")
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                bootstrap("copilot", root, force=True)
            on_disk = skill.read_text(encoding="utf-8")
            self.assertNotIn("OPERATOR HAND-EDIT", on_disk)
            self.assertIn("Default starting point", on_disk)
            self.assertIn("clobbered", buf.getvalue())
            self.assertIn("retrieval-commands", buf.getvalue())

    def test_missing_region_appended_at_eof(self) -> None:
        # Use replace_region_bodies + append_region directly: the public
        # writer never produces "missing region" naturally for bundled
        # templates because seed-then-edit always preserves the region set.
        existing = (
            "front matter\n"
            "<!-- weld-managed:start name=alpha -->\nA-on-disk\n"
            "<!-- weld-managed:end name=alpha -->\n"
            "operator notes\n"
        )
        template = (
            "front matter\n"
            "<!-- weld-managed:start name=alpha -->\nA-on-disk\n"
            "<!-- weld-managed:end name=alpha -->\n"
            "<!-- weld-managed:start name=beta -->\nB-template\n"
            "<!-- weld-managed:end name=beta -->\n"
        )
        new_text = append_region(existing, template, "beta")
        self.assertIn("operator notes", new_text)
        self.assertIn("B-template", new_text)
        # One blank-line separator between operator content and appended block.
        self.assertIn("operator notes\n\n<!-- weld-managed:start name=beta -->", new_text)

    def test_replace_region_bodies_only_overwrites_named(self) -> None:
        existing = (
            "<!-- weld-managed:start name=keep -->\noperator\n"
            "<!-- weld-managed:end name=keep -->\n"
            "<!-- weld-managed:start name=swap -->\nold\n"
            "<!-- weld-managed:end name=swap -->\n"
        )
        template = (
            "<!-- weld-managed:start name=keep -->\nupstream-keep\n"
            "<!-- weld-managed:end name=keep -->\n"
            "<!-- weld-managed:start name=swap -->\nupstream-swap\n"
            "<!-- weld-managed:end name=swap -->\n"
        )
        new_text = replace_region_bodies(existing, template, ["swap"])
        self.assertIn("operator", new_text)        # untouched
        self.assertIn("upstream-swap", new_text)   # overwritten
        self.assertNotIn("old", new_text)
        self.assertNotIn("upstream-keep", new_text)


class DiffFlagTest(unittest.TestCase):
    """``--diff`` is region-scoped by default; ``--include-unmanaged`` opts out."""

    def test_diff_with_curated_outside_region_is_silent(self) -> None:
        """User-reported case: a single curated line outside any managed
        region must yield empty ``--diff`` output and exit 0.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("copilot", root, force=True)
            skill = root / ".github" / "skills" / "weld" / "SKILL.md"
            text = skill.read_text(encoding="utf-8")
            # Operator inserts a line OUTSIDE any managed region: under the
            # "## When to use it" bullet list, which the templates do not wrap.
            text = text.replace(
                "## When to use it\n",
                "## When to use it\n\n- Curated trigger phrase for this repo\n",
            )
            skill.write_text(text, encoding="utf-8")
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                with self.assertRaises(SystemExit) as cm:
                    cli_main(["bootstrap", "copilot", "--root", str(root), "--diff"])
            self.assertEqual(cm.exception.code, 0)
            output = buf.getvalue()
            # No unified-diff markers in the regional output.
            self.assertNotIn("---", output)
            self.assertNotIn("+++", output)

    def test_diff_with_in_region_edit_emits_region_diff(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("copilot", root, force=True)
            skill = root / ".github" / "skills" / "weld" / "SKILL.md"
            text = skill.read_text(encoding="utf-8")
            text = text.replace(
                "Default starting point",
                "OPERATOR HAND-EDIT",
            )
            skill.write_text(text, encoding="utf-8")
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                with self.assertRaises(SystemExit) as cm:
                    cli_main(["bootstrap", "copilot", "--root", str(root), "--diff"])
            self.assertEqual(cm.exception.code, 1)
            output = buf.getvalue()
            self.assertIn("[region=retrieval-commands]", output)
            # The on-disk hand-edit appears on the "-" side of the diff and
            # the template original on the "+" side.
            self.assertIn("OPERATOR HAND-EDIT", output)
            self.assertIn("Default starting point", output)

    def test_diff_include_unmanaged_falls_back_to_whole_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("copilot", root, force=True)
            skill = root / ".github" / "skills" / "weld" / "SKILL.md"
            text = skill.read_text(encoding="utf-8")
            text = text.replace(
                "## When to use it\n",
                "## When to use it\n\n- Curated trigger phrase for this repo\n",
            )
            skill.write_text(text, encoding="utf-8")
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                with self.assertRaises(SystemExit) as cm:
                    cli_main([
                        "bootstrap", "copilot", "--root", str(root),
                        "--diff", "--include-unmanaged",
                    ])
            # --include-unmanaged surfaces the whole-file diff, so the curated
            # line shows up and the exit code is 1.
            self.assertEqual(cm.exception.code, 1)
            output = buf.getvalue()
            self.assertIn("Curated trigger phrase", output)


class TemplateLintTest(unittest.TestCase):
    """Every bundled template parses cleanly and siblings declare matching regions."""

    def test_lint_reports_no_errors(self) -> None:
        errors = lint_template_dir(_TEMPLATES_DIR)
        self.assertEqual(errors, [], f"template lint errors: {errors}")

    def test_every_md_template_parses(self) -> None:
        for path in _TEMPLATES_DIR.glob("*.md"):
            text = path.read_text(encoding="utf-8")
            try:
                parse_regions(text)
            except MarkerError as exc:
                self.fail(f"{path.name}: {exc}")

    def test_sibling_variants_declare_same_regions(self) -> None:
        for md in _TEMPLATES_DIR.glob("*.md"):
            if md.name.endswith(".cli.md"):
                continue
            cli = md.with_name(md.stem + ".cli.md")
            if not cli.is_file():
                continue
            md_names = {r.name for r in parse_regions(md.read_text(encoding="utf-8"))}
            cli_names = {r.name for r in parse_regions(cli.read_text(encoding="utf-8"))}
            self.assertEqual(
                md_names, cli_names,
                f"sibling-variant region mismatch: {md.name} vs {cli.name}",
            )

    def test_federation_paragraph_parses_as_single_region(self) -> None:
        regions = parse_regions(_FEDERATION_PARAGRAPH)
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0].name, "federation")


if __name__ == "__main__":
    unittest.main()
