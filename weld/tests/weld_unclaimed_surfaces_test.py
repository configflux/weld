"""The surfaces that report unclaimed source (ADR 0135, ADR 0144).

What the rule *is* lives beside it in ``weld_unclaimed_sources_test.py``, which
is pure and never touches a filesystem. This module is the other half: the
read-only disk walk that feeds the rule, and the three places its answer
surfaces -- the ``wd doctor`` row (warn, exit 0, a note id ``--ack`` accepts),
the ``wd prime`` line and next step, and the version stamp ``wd init`` writes.
Split from that module when the ADR 0144 claim cases pushed the pair past the
400-line cap; the seam is where the tests stop being about the rule and start
being about the commands.

Field eval v0.23.1 Finding 05 -- a child with 8 ``.cs`` files and a
markdown-only config, healthy to every diagnostic while 100% of its source was
invisible -- is pinned as the regression at the bottom of this file.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld._unclaimed_sources import (
    check_unclaimed_sources,
    detect_unclaimed_source_classes,
    prime_unclaimed_lines,
)


class _Result:
    """Stand-in for weld.doctor.CheckResult with the fields the check sets."""

    def __init__(self, level, message, section="Project", note_id=None):
        self.level = level
        self.message = message
        self.section = section
        self.note_id = note_id


def _write_config(root: Path, body: str) -> None:
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    (root / ".weld" / "discover.yaml").write_text(body, encoding="utf-8")


def _assert_refresh_offered_first(case: unittest.TestCase, text: str) -> None:
    """Both remedies are named, and the non-destructive one comes first.

    ``--force`` regenerates the config from scratch and discards hand edits;
    ``--refresh`` merges and keeps them. A warning that names only ``--force``
    (or names it first) tells a maintainer following it to throw their own
    edits away, so the order is asserted, not just the presence.
    """
    case.assertIn("wd init --refresh", text, text)
    case.assertIn("wd init --force", text, text)
    case.assertLess(
        text.index("wd init --refresh"), text.index("wd init --force"),
        f"the destructive remedy is offered first: {text}",
    )

class UnclaimedDiskWalkTest(unittest.TestCase):
    """The read-only disk walk that backs the doctor/prime surfaces."""

    def test_no_weld_dir_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(detect_unclaimed_source_classes(Path(d)), [])

    def test_disabled_entry_does_not_claim(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write_config(
                root,
                "sources:\n  - glob: \"**/*.cs\"\n    type: file\n"
                "    strategy: tree_sitter\n    enabled: false\n",
            )
            (root / "A.cs").write_text("class A {}\n", encoding="utf-8")
            got = detect_unclaimed_source_classes(root)
            self.assertEqual([u.language for u in got], ["csharp"])

    def test_claimed_language_on_disk_is_silent(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write_config(
                root,
                "sources:\n  - glob: \"**/*.cs\"\n    type: file\n"
                "    strategy: tree_sitter\n    language: csharp\n",
            )
            (root / "A.cs").write_text("class A {}\n", encoding="utf-8")
            self.assertEqual(detect_unclaimed_source_classes(root), [])


class DoctorSurfaceTest(unittest.TestCase):
    def test_warn_carries_stable_note_id_and_section(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write_config(
                root,
                "sources:\n  - glob: \"doc/*.md\"\n    type: doc\n"
                "    strategy: markdown\n",
            )
            (root / "A.cs").write_text("class A {}\n", encoding="utf-8")
            results = check_unclaimed_sources(root, _Result)
            self.assertEqual(len(results), 1)
            r = results[0]
            self.assertEqual(r.level, "warn")
            self.assertEqual(r.section, "Config")
            self.assertEqual(r.note_id, "unclaimed-source-csharp")
            _assert_refresh_offered_first(self, r.message)

    def test_doctor_exit_stays_zero_on_unclaimed(self):
        # A stale config is a warn, never a fail: it must not break automation
        # that keys on doctor's exit status.
        from weld.doctor import doctor

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write_config(
                root,
                "sources:\n  - glob: \"doc/*.md\"\n    type: doc\n"
                "    strategy: markdown\n",
            )
            (root / "A.cs").write_text("class A {}\n", encoding="utf-8")
            (root / ".weld" / "graph.json").write_text(
                '{"meta":{"schema_version":4},"nodes":{},"edges":[]}',
                encoding="utf-8",
            )
            results = doctor(root)
            self.assertFalse(any(r.level == "fail" for r in results))
            self.assertTrue(
                any("no wired strategy" in r.message for r in results)
            )


class SuppressionTest(unittest.TestCase):
    """The warn is dismissible via the existing --ack machinery (ADR 0135)."""

    def test_unclaimed_id_is_a_valid_ack_target(self):
        from weld._doctor_suppressions import _is_valid_note_id

        self.assertTrue(_is_valid_note_id("unclaimed-source-csharp"))
        self.assertFalse(_is_valid_note_id("unclaimed-source"))
        self.assertFalse(_is_valid_note_id("bogus-id"))

    def test_warn_row_renders_its_id_and_is_suppressible(self):
        from weld._doctor_format import apply_suppressions, format_line

        row = _Result(
            "warn", "8 C# files present ...", "Config",
            note_id="unclaimed-source-csharp",
        )
        # The id is rendered so a user can copy it into --ack, even though the
        # row is a warn rather than a note.
        self.assertIn("(id: unclaimed-source-csharp)", format_line(row))
        # And acking it drops the row.
        kept = apply_suppressions([row], {"unclaimed-source-csharp"})
        self.assertEqual(kept, [])

    def test_unidded_rows_are_never_dropped(self):
        from weld._doctor_format import apply_suppressions

        plain = _Result("warn", "some other warning", "Graph")
        self.assertEqual(
            apply_suppressions([plain], {"unclaimed-source-csharp"}), [plain],
        )


class PrimeSurfaceTest(unittest.TestCase):
    def test_prime_emits_line_and_next_step(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write_config(
                root,
                "sources:\n  - glob: \"doc/*.md\"\n    type: doc\n"
                "    strategy: markdown\n",
            )
            (root / "A.cs").write_text("class A {}\n", encoding="utf-8")

            def _status(tag, msg):
                return f"[{tag}] {msg}"

            lines, steps = prime_unclaimed_lines(root, _status)
            # A next step is a command to run, so it is the non-destructive
            # one; the WARN line above it still names both.
            self.assertEqual(steps, ["wd init --refresh"])
            self.assertTrue(any("no wired strategy" in ln for ln in lines))
            _assert_refresh_offered_first(self, "\n".join(lines))

    def test_prime_end_to_end_lists_unclaimed(self):
        from weld.prime import prime

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write_config(
                root,
                "sources:\n  - glob: \"doc/*.md\"\n    type: doc\n"
                "    strategy: markdown\n",
            )
            (root / "A.cs").write_text("class A {}\n", encoding="utf-8")
            out = prime(root)
            self.assertIn("no wired strategy", out)
            _assert_refresh_offered_first(self, out)
            _, _, next_steps = out.partition("Next steps:")
            self.assertIn("wd init --refresh", next_steps, out)


class VersionStampTest(unittest.TestCase):
    def test_generated_yaml_stamps_weld_version(self):
        from weld.init import generate_yaml

        text = generate_yaml(
            languages={}, frameworks=[], dockerfiles=[], compose_files=[],
            ci_files=[], claude_agents=[], claude_commands=[], doc_dirs=[],
            python_globs=[], root_configs=[],
        )
        self.assertIn("generated-by: weld", text)


class Finding05RegressionTest(unittest.TestCase):
    """The evaluator's exact case: 8 .cs files, markdown-only config.

    Before this change every such repo passed ``wd doctor`` with zero warnings
    while 100% of its source was invisible. This pins ADR 0135's contract --
    both surfaces now name the invisible language and point at
    ``wd init --refresh`` before the destructive ``wd init --force``.
    """

    def test_eight_cs_files_markdown_only_config(self):
        from weld.doctor import doctor
        from weld.prime import prime

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write_config(
                root,
                "sources:\n  - glob: \"doc/*.md\"\n    type: doc\n"
                "    strategy: markdown\n",
            )
            src = root / "src"
            src.mkdir()
            for i in range(8):
                (src / f"C{i}.cs").write_text(
                    f"class C{i} {{}}\n", encoding="utf-8",
                )
            (root / "app.csproj").write_text("<Project/>\n", encoding="utf-8")
            # The eval repos each had a (thin) graph -- doctor's missing-graph
            # fail is a separate concern; give it a graph so this pins only the
            # unclaimed-source behavior.
            (root / ".weld" / "graph.json").write_text(
                '{"meta":{"schema_version":4},"nodes":{"a":{}},"edges":[]}',
                encoding="utf-8",
            )

            doctor_msgs = [r.message for r in doctor(root)]
            self.assertTrue(
                any(
                    "8 C# files present that no wired strategy" in m
                    for m in doctor_msgs
                ),
                doctor_msgs,
            )
            self.assertFalse(any(r.level == "fail" for r in doctor(root)))

            out = prime(root)
            self.assertIn("8 C# files present that no wired strategy", out)
            _assert_refresh_offered_first(self, out)


if __name__ == "__main__":
    unittest.main()
