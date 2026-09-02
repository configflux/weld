"""The record of what weld wired, and the entry-shaped offer it gates.

The system-level probe for this contract is
``weld_init_refresh_entries_e2e_test``, which drives the real CLI over the
issue's tree. These are the unit-level cases it deliberately does not carry:
the record's own read/write behaviour against a file that is *someone else's*
(a neighbouring comment must survive a rewrite; a second rewrite must not
stack a second record), the key extraction from each entry shape a
``discover.yaml`` may use, and the migration -- a config written before the
record existed seeds itself from its own entries, once, so that the removal
which follows is durable (bd 5038-j5o5d).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld._init_entry_offer import EntryWiring, entry_blocks, entry_keys
from weld._init_refresh import refresh
from weld._init_wired_ledger import (
    apply_ledger,
    config_entry_keys,
    ledger_keys,
    render_ledger,
)
from weld._yaml import parse_yaml

#: A minimal config with the stamp the record anchors under.
STAMPED = """\
# .weld/discover.yaml
#
# generated-by: weld 0.24.0
sources:
  - files: ["package.json"]
    type: config
    strategy: config_file
"""

#: The same config with no stamp at all -- the pre-0.24 shape, where the
#: record has to fall back to the ``sources:`` line as its anchor.
UNSTAMPED = """\
# Hand-maintained. Do not clobber.
sources:
  - files: ["package.json"]
    type: config
    strategy: config_file
"""

_KEYS = {("config_file", "package.json"), ("express", "**/*.{ts,tsx}")}


class LedgerRoundTripTest(unittest.TestCase):
    """What is written is what is read back."""

    def test_a_config_with_no_record_reads_as_empty(self) -> None:
        self.assertEqual(ledger_keys(STAMPED), set())

    def test_render_then_read_returns_the_same_keys(self) -> None:
        self.assertEqual(ledger_keys(render_ledger(_KEYS)), _KEYS)

    def test_an_empty_key_set_renders_nothing(self) -> None:
        self.assertEqual(render_ledger(set()), "")

    def test_keys_are_rendered_sorted(self) -> None:
        """Stable bytes, so a refresh that wired nothing leaves no diff."""
        first, _ = apply_ledger(STAMPED, _KEYS)
        second, _ = apply_ledger(STAMPED, set(reversed(sorted(_KEYS))))
        self.assertEqual(first, second)

    def test_a_target_containing_spaces_survives_the_round_trip(self) -> None:
        keys = {("config_file", "my config.json")}
        self.assertEqual(ledger_keys(render_ledger(keys)), keys)


class LedgerPlacementTest(unittest.TestCase):
    """The record lands somewhere defensible, or nowhere at all."""

    def test_it_is_written_under_the_version_stamp(self) -> None:
        text, changed = apply_ledger(STAMPED, _KEYS)
        self.assertTrue(changed)
        lines = text.splitlines()
        stamp = lines.index("# generated-by: weld 0.24.0")
        self.assertTrue(lines[stamp + 1].startswith("# wired-entries:"))

    def test_it_falls_back_to_the_sources_line(self) -> None:
        text, changed = apply_ledger(UNSTAMPED, _KEYS)
        self.assertTrue(changed)
        lines = text.splitlines()
        self.assertLess(
            lines.index("# wired-entry: config_file package.json"),
            lines.index("sources:"),
        )

    def test_a_config_with_neither_anchor_is_left_alone(self) -> None:
        """Nowhere to put it that would not be a guess about someone's file."""
        text, changed = apply_ledger("# just a comment\n", _KEYS)
        self.assertFalse(changed)
        self.assertEqual(text, "# just a comment\n")

    def test_a_rewrite_replaces_the_record_rather_than_stacking_one(self) -> None:
        once, _ = apply_ledger(STAMPED, _KEYS)
        twice, _ = apply_ledger(once, _KEYS)
        self.assertEqual(once, twice)
        self.assertEqual(twice.count("# wired-entries:"), 1)

    def test_a_shrinking_record_drops_the_keys_it_no_longer_holds(self) -> None:
        once, _ = apply_ledger(STAMPED, _KEYS)
        fewer, _ = apply_ledger(once, {("config_file", "package.json")})
        self.assertEqual(ledger_keys(fewer), {("config_file", "package.json")})

    def test_clearing_the_record_removes_its_lines(self) -> None:
        once, _ = apply_ledger(STAMPED, _KEYS)
        cleared, changed = apply_ledger(once, set())
        self.assertTrue(changed)
        self.assertEqual(cleared, STAMPED)

    def test_a_neighbouring_comment_survives_a_rewrite(self) -> None:
        """The record is line-shaped so a rewrite cannot swallow a neighbour."""
        text, _ = apply_ledger(STAMPED, _KEYS)
        annotated = text.replace(
            "sources:", "# a note the maintainer wrote\nsources:", 1)
        rewritten, _ = apply_ledger(annotated, {("express", "**/*.{ts,tsx}")})
        self.assertIn("# .weld/discover.yaml", rewritten)
        self.assertIn("# a note the maintainer wrote", rewritten)

    def test_the_record_is_inert_to_the_config_parser(self) -> None:
        """Comment lines, so no consumer of the config sees anything new."""
        text, _ = apply_ledger(STAMPED, _KEYS)
        self.assertEqual(parse_yaml(text), parse_yaml(STAMPED))


class ConfigEntryKeyTest(unittest.TestCase):
    """Every entry shape a discover.yaml may use, keyed the way it wires."""

    def test_a_files_entry_contributes_one_key_per_name(self) -> None:
        keys = config_entry_keys(
            'sources:\n'
            '  - files: ["package.json", "tsconfig.json"]\n'
            '    type: config\n'
            '    strategy: config_file\n'
        )
        self.assertEqual(keys, {
            ("config_file", "package.json"), ("config_file", "tsconfig.json"),
        })

    def test_a_glob_entry_contributes_its_pattern(self) -> None:
        keys = config_entry_keys(
            'sources:\n'
            '  - glob: "**/*.{ts,tsx}"\n'
            '    type: file\n'
            '    strategy: express\n'
        )
        self.assertEqual(keys, {("express", "**/*.{ts,tsx}")})

    def test_a_path_entry_contributes_its_path(self) -> None:
        keys = config_entry_keys(
            'sources:\n'
            '  - path: "docs/readme.md"\n'
            '    type: doc\n'
            '    strategy: markdown\n'
        )
        self.assertEqual(keys, {("markdown", "docs/readme.md")})

    def test_a_disabled_entry_still_counts_as_carried(self) -> None:
        """Disabling an entry is a decision about it; re-offering undoes it."""
        keys = config_entry_keys(
            'sources:\n'
            '  - glob: "**/*.go"\n'
            '    type: route\n'
            '    strategy: gin\n'
            '    enabled: false\n'
        )
        self.assertEqual(keys, {("gin", "**/*.go")})

    def test_an_unreadable_config_carries_nothing(self) -> None:
        self.assertEqual(config_entry_keys("sources: [oh dear\n"), set())


class EntryOfferTest(unittest.TestCase):
    """What the detector table offers, and what it withholds."""

    def _node_wiring(self, **kwargs) -> EntryWiring:
        return EntryWiring(
            root_configs=("package.json", "tsconfig.json"),
            frameworks=(("Express", "express", "src/server.ts"),),
            languages=frozenset({"typescript"}),
            **kwargs,
        )

    def test_keys_cover_every_root_config_and_framework_entry(self) -> None:
        keys = set(entry_keys(self._node_wiring()))
        self.assertIn(("config_file", "package.json"), keys)
        self.assertIn(("config_file", "tsconfig.json"), keys)
        self.assertIn(
            ("express", "**/*.{ts,tsx,js,jsx,mjs,cjs}"), keys)

    def test_a_known_key_is_not_offered(self) -> None:
        known = frozenset({("config_file", "package.json")})
        blocks, keys = entry_blocks(self._node_wiring(), known)
        self.assertNotIn(("config_file", "package.json"), keys)
        self.assertIn(("config_file", "tsconfig.json"), keys)
        self.assertNotIn("package.json", "".join(blocks))

    def test_the_surviving_root_configs_collapse_into_one_entry(self) -> None:
        """The shape a full init writes, not one block per file name."""
        blocks, _ = entry_blocks(self._node_wiring(), frozenset())
        config_blocks = [b for b in blocks if "strategy: config_file" in b]
        self.assertEqual(len(config_blocks), 1, blocks)
        self.assertIn(
            '- files: ["package.json", "tsconfig.json"]', config_blocks[0])

    def test_everything_known_offers_nothing(self) -> None:
        wiring = self._node_wiring()
        blocks, keys = entry_blocks(wiring, frozenset(entry_keys(wiring)))
        self.assertEqual((blocks, keys), ([], []))

    def test_a_framework_whose_language_is_absent_is_not_offered(self) -> None:
        """The same gate ``wd init`` applies, so refresh is its subset."""
        wiring = EntryWiring(
            frameworks=(("Express", "express", "src/server.ts"),),
            languages=frozenset({"go"}),
        )
        self.assertEqual(entry_keys(wiring), [])


class MigrationTest(unittest.TestCase):
    """A config written before the record existed seeds itself from its own."""

    #: Two root configs on disk; the config names one of them and carries no
    #: record at all -- every ``discover.yaml`` in the field, before this.
    TREE = {
        "package.json": '{"name": "app"}\n',
        "tsconfig.json": '{"compilerOptions": {}}\n',
    }

    def _refresh(self, config: str, tree: dict[str, str] | None = None):
        """Run one refresh over a fresh temp repo; return the RefreshResult."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel, body in (tree or self.TREE).items():
                (root / rel).write_text(body, encoding="utf-8")
            (root / ".weld").mkdir()
            out = root / ".weld" / "discover.yaml"
            out.write_text(config, encoding="utf-8")
            result = refresh(root, out)
        assert result is not None
        return result

    def test_an_unrecorded_config_is_offered_what_it_lacks(self) -> None:
        result = self._refresh(STAMPED)
        self.assertEqual(
            list(result.entries), [("config_file", "tsconfig.json")])

    def test_the_refreshed_config_records_both_keys(self) -> None:
        """Seeded from what it carried, extended by what was just wired."""
        result = self._refresh(STAMPED)
        self.assertEqual(ledger_keys(result.new_text), {
            ("config_file", "package.json"), ("config_file", "tsconfig.json"),
        })

    def test_a_carried_entry_is_seeded_rather_than_re_offered(self) -> None:
        carried = STAMPED.replace(
            '["package.json"]', '["package.json", "tsconfig.json"]')
        result = self._refresh(carried)
        self.assertEqual(result.entries, ())
        self.assertIn(("config_file", "tsconfig.json"),
                      ledger_keys(result.new_text))

    def test_the_seeded_key_makes_the_next_removal_durable(self) -> None:
        """The migration is one-time: remove it once more and it stays out."""
        carried = STAMPED.replace(
            '["package.json"]', '["package.json", "tsconfig.json"]')
        seeded = self._refresh(carried).new_text
        removed = seeded.replace(
            '["package.json", "tsconfig.json"]', '["package.json"]')
        self.assertEqual(self._refresh(removed).entries, ())

    def test_a_config_recording_a_key_it_does_not_carry_is_not_re_offered(
        self,
    ) -> None:
        """The whole point: absent-but-recorded is a hand removal."""
        recorded, _ = apply_ledger(
            STAMPED, {("config_file", "tsconfig.json")})
        self.assertEqual(self._refresh(recorded).entries, ())


if __name__ == "__main__":
    unittest.main()
