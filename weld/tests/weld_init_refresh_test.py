"""Regression tests for ``wd init --refresh`` (field eval v0.23.1 Finding 05).

``wd init --force`` is the only remediation for a stale ``discover.yaml``, but it
regenerates from scratch and discards every hand edit. ``--refresh`` is the
non-destructive middle path: it wires the strategies for languages present on
disk that no wired strategy claims (ADR 0135 drift unit) while preserving the
user's existing config byte-for-byte.

The three cases the task names are pinned here plus the two edge cases the merge
semantics turn on:

1. **fresh-config** (nothing unclaimed): entries untouched, version stamp bumped,
   ``wired`` empty -- the "already current" no-op.
2. **hand-edited-config**: custom globs / strategies / comments preserved
   verbatim; the unclaimed languages appended after them.
3. **recognised-nothing** (all-stub config + real source on disk): the unclaimed
   languages get wired.
4. **missing-config**: refresh returns ``None`` (edits, never creates).
5. **stamp**: an existing stamp is rewritten; a pre-stamp config gains one.
"""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from weld import init as init_mod
from weld._init_refresh import refresh
from weld._yaml import parse_yaml


def _wired_strategies(text: str) -> set[str]:
    """Strategy names of every *uncommented* source entry in ``text``."""
    data = parse_yaml(text)
    sources = data.get("sources", []) if isinstance(data, dict) else []
    return {
        s.get("strategy")
        for s in sources
        if isinstance(s, dict) and s.get("strategy")
    }


def _write(root: Path, config: str) -> Path:
    """Create ``root/.weld/discover.yaml`` with ``config`` and return its path."""
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    out = root / ".weld" / "discover.yaml"
    out.write_text(config, encoding="utf-8")
    return out


class FreshConfigNoOpTest(unittest.TestCase):
    """A config that already claims every on-disk language is a no-op."""

    def test_no_unclaimed_language_wires_nothing_but_bumps_stamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("x = 1\n", encoding="utf-8")
            out = _write(root, (
                "# generated-by: weld 0.20.0\n"
                "sources:\n"
                '  - glob: "src/**/*.py"\n'
                "    type: file\n"
                "    strategy: python_module\n"
            ))
            result = refresh(root, out)
            self.assertIsNotNone(result)
            assert result is not None  # narrow for type-checkers
            # No language is unclaimed, so nothing is wired.
            self.assertEqual(result.wired, ())
            self.assertNotIn("refresh (wd init", result.new_text)
            # ... but the stale stamp is bumped to current.
            self.assertTrue(result.stamp_updated)
            self.assertNotIn("weld 0.20.0", result.new_text)
            # The user's single python_module entry is still the only source.
            self.assertEqual(_wired_strategies(result.new_text), {"python_module"})


class HandEditedConfigPreservedTest(unittest.TestCase):
    """Hand edits survive; only unclaimed languages are appended."""

    def test_custom_entries_preserved_and_unclaimed_languages_appended(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("x = 1\n", encoding="utf-8")
            (root / "Service.cs").write_text("class S {}\n", encoding="utf-8")
            (root / "README.md").write_text("# docs\n", encoding="utf-8")
            hand_edited = (
                "# my hand edits -- do not clobber\n"
                "# custom exclusion note: vendor/ intentionally ungraphed\n"
                "sources:\n"
                "  # ===== docs =====\n"
                '  - glob: "**/*.md"\n'
                "    type: doc\n"
                "    strategy: markdown\n"
                "    id_prefix: my-custom-prefix\n"
            )
            out = _write(root, hand_edited)
            result = refresh(root, out)
            assert result is not None
            # Every hand-edited line survives verbatim as a prefix of the output.
            self.assertTrue(
                result.new_text.startswith("# my hand edits -- do not clobber"))
            self.assertIn("custom exclusion note: vendor/", result.new_text)
            self.assertIn("id_prefix: my-custom-prefix", result.new_text)
            # The user's markdown entry is intact alongside the new ones.
            strategies = _wired_strategies(result.new_text)
            self.assertIn("markdown", strategies)
            # python + csharp were unclaimed on disk -> both wired.
            self.assertEqual(
                sorted(w.language for w in result.wired), ["csharp", "python"])
            self.assertIn("python_module", strategies)
            self.assertIn("tree_sitter", strategies)
            # Output remains valid YAML.
            self.assertIsInstance(parse_yaml(result.new_text), dict)

    def test_disk_write_matches_returned_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("x = 1\n", encoding="utf-8")
            out = _write(root, "sources:\n  # ===== docs =====\n")
            result = refresh(root, out)
            assert result is not None
            self.assertEqual(out.read_text(encoding="utf-8"), result.new_text)


class RecognisedNothingConfigTest(unittest.TestCase):
    """An all-stub config over real source wires the unclaimed languages."""

    def test_all_stub_config_wires_present_language(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main.go").write_text("package main\n", encoding="utf-8")
            # A config that wires nothing -- only a commented-out stub.
            stub_only = (
                "sources:\n"
                "  # ===== code (uncomment to enable) =====\n"
                '  # - glob: "src/**/*.py"\n'
                "  #   type: file\n"
                "  #   strategy: python_module\n"
            )
            out = _write(root, stub_only)
            result = refresh(root, out)
            assert result is not None
            self.assertEqual([w.language for w in result.wired], ["go"])
            strategies = _wired_strategies(result.new_text)
            self.assertIn("tree_sitter", strategies)
            # The commented stub is left untouched (still commented).
            self.assertIn('  # - glob: "src/**/*.py"', result.new_text)


class MissingConfigTest(unittest.TestCase):
    """Refresh edits an existing config; it never creates one."""

    def test_missing_discover_yaml_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".weld").mkdir()
            (root / "app.py").write_text("x = 1\n", encoding="utf-8")
            out = root / ".weld" / "discover.yaml"
            self.assertIsNone(refresh(root, out))
            # Nothing was created.
            self.assertFalse(out.exists())


class StampInteractionTest(unittest.TestCase):
    """The version stamp is the visible signal a refresh ran (Finding 05)."""

    def test_existing_stamp_rewritten_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("x = 1\n", encoding="utf-8")
            out = _write(root, (
                "# header\n"
                "#\n"
                "# generated-by: weld 0.19.0\n"
                "sources:\n"
                '  - glob: "src/**/*.py"\n'
                "    type: file\n"
                "    strategy: python_module\n"
            ))
            result = refresh(root, out)
            assert result is not None
            self.assertTrue(result.stamp_updated)
            self.assertNotIn("weld 0.19.0", result.new_text)
            # Exactly one stamp line -- rewritten, not duplicated.
            stamps = [
                ln for ln in result.new_text.splitlines()
                if ln.startswith("# generated-by: weld ")
            ]
            self.assertEqual(len(stamps), 1)

    def test_prestamp_config_gains_a_stamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("x = 1\n", encoding="utf-8")
            out = _write(root, (
                "# a config from before stamps existed\n"
                "sources:\n"
                '  - glob: "src/**/*.py"\n'
                "    type: file\n"
                "    strategy: python_module\n"
            ))
            result = refresh(root, out)
            assert result is not None
            self.assertTrue(result.stamp_updated)
            self.assertIn("# generated-by: weld ", result.new_text)
            # Inserted above sources:, so the config still parses.
            self.assertIsInstance(parse_yaml(result.new_text), dict)


class CliWiringTest(unittest.TestCase):
    """``wd init --refresh`` at the CLI: idempotent, exclusive, well-reported."""

    def test_force_and_refresh_are_mutually_exclusive(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            init_mod.main(["--force", "--refresh"])

    def test_missing_config_points_at_wd_init(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".weld").mkdir()
            buf = io.StringIO()
            with redirect_stderr(buf):
                init_mod.main(["--refresh", str(root)])
            self.assertIn("run `wd init` first", buf.getvalue())

    def test_cli_refresh_wires_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "sources:\n  # ===== docs =====\n")
            (root / "main.go").write_text("package main\n", encoding="utf-8")
            out = root / ".weld" / "discover.yaml"

            with redirect_stderr(io.StringIO()):
                init_mod.main(["--refresh", str(root)])
            after_first = out.read_text(encoding="utf-8")
            self.assertIn("tree_sitter", after_first)
            self.assertEqual(after_first.count("refresh (wd init"), 1)

            # A second refresh finds nothing unclaimed: no duplicate block.
            with redirect_stderr(io.StringIO()):
                init_mod.main(["--refresh", str(root)])
            self.assertEqual(
                out.read_text(encoding="utf-8").count("refresh (wd init"), 1)


if __name__ == "__main__":
    unittest.main()
