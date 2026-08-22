"""``config_file`` names its files by ``glob`` as well as by ``files`` (bd q85a).

The strategy mints one ``config:`` node per named file -- a static data file
the program reads at runtime, whose content decides behaviour. It could only
be pointed at an explicit ``files:`` list, which is why weld's own shipped
data (``weld/languages/*.yaml``, ``weld/templates/*``) had no nodes: the only
way to cover a directory was to enumerate it, and this repo has already paid
for that lesson once (bd crau -- a hand-listed set leaves the *next* file
invisible, and it stays invisible until somebody trips over it).

``.weld/discover.yaml``'s own resolver has always accepted ``glob``, ``path``
and ``files`` interchangeably, so a ``glob`` entry was already being resolved
for incremental hashing and ADR 0101 coverage while this strategy read none of
it -- the config surface promised something the strategy did not honour.

The load-bearing property, and the reason the glob half calls ``walk_glob``
with the entry's excludes and nothing else: the set this strategy *emits* must
equal the set :func:`weld._source_resolve.resolve_source_files` *records*. A
second, differently-spelled filter here would leave files recorded as in-scope
that no node covers, which reads as permanently uncovered scope -- staleness
that never clears and a full discovery on every read.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from weld._source_resolve import resolve_source_files
from weld.strategies import config_file


class ConfigFileGlobTest(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "packs").mkdir()
        for name in ("alpha.yaml", "beta.yaml", "notes.md"):
            (self.root / "packs" / name).write_text("x: 1\n", encoding="utf-8")
        (self.root / "packs" / "nested").mkdir()
        (self.root / "packs" / "nested" / "deep.yaml").write_text(
            "x: 1\n", encoding="utf-8")
        (self.root / "root.toml").write_text("a = 1\n", encoding="utf-8")

    def _extract(self, source: dict) -> dict:
        return config_file.extract(self.root, source, {}).nodes

    def test_a_glob_mints_a_node_per_match(self) -> None:
        nodes = self._extract({"glob": "packs/*.yaml", "type": "config"})
        self.assertEqual(
            sorted(nodes),
            ["config:packs_alpha_yaml", "config:packs_beta_yaml"],
        )

    def test_the_node_carries_the_config_shape(self) -> None:
        node = self._extract({"glob": "packs/alpha.yaml"})["config:packs_alpha_yaml"]
        self.assertEqual(node["type"], "config")
        self.assertEqual(node["label"], "alpha.yaml")
        self.assertEqual(node["props"]["file"], "packs/alpha.yaml")
        self.assertEqual(node["props"]["source_strategy"], "config_file")
        self.assertEqual(node["props"]["roles"], ["config"])

    def test_a_single_star_does_not_span_a_directory_separator(self) -> None:
        """The same glob semantics every other entry gets."""
        nodes = self._extract({"glob": "packs/*.yaml"})
        self.assertNotIn("config:packs_nested_deep_yaml", nodes)

    def test_excludes_are_honoured(self) -> None:
        nodes = self._extract(
            {"glob": "packs/*", "exclude": ["packs/*.md", "packs/beta.yaml"]})
        self.assertEqual(sorted(nodes), ["config:packs_alpha_yaml"])

    def test_a_matched_directory_is_not_a_config_file(self) -> None:
        """``packs/*`` matches ``packs/nested`` -- a node for it would be junk.

        A single-directory glob resolves through ``Path.glob``, which yields
        directories as well as files, while the ``**`` branch of ``walk_glob``
        yields only files. Whatever that asymmetry deserves, this strategy
        cannot mint a ``config:`` node for a path no reader can open and that
        ``build_file_hashes`` drops from the incremental basis anyway.
        """
        nodes = self._extract({"glob": "packs/*"})
        self.assertNotIn("config:packs_nested", nodes)
        self.assertEqual(
            sorted(nodes),
            ["config:packs_alpha_yaml", "config:packs_beta_yaml",
             "config:packs_notes_md"],
        )

    def test_a_files_entry_naming_a_directory_is_skipped(self) -> None:
        self.assertEqual(self._extract({"files": ["packs"]}), {})

    def test_files_still_works_and_composes_with_glob(self) -> None:
        """The original shape is untouched, and the two keys add up."""
        self.assertEqual(
            sorted(self._extract({"files": ["root.toml"]})),
            ["config:root_toml"],
        )
        self.assertEqual(
            sorted(self._extract({"glob": "packs/*.yaml", "files": ["root.toml"]})),
            ["config:packs_alpha_yaml", "config:packs_beta_yaml",
             "config:root_toml"],
        )

    def test_an_entry_naming_nothing_emits_nothing(self) -> None:
        self.assertEqual(self._extract({"glob": "nowhere/*.yaml"}), {})
        self.assertEqual(self._extract({"type": "config"}), {})

    def test_every_matched_file_is_recorded_as_discovered_from(self) -> None:
        """Provenance, per file -- what makes editing one mark the graph stale."""
        result = config_file.extract(self.root, {"glob": "packs/*.yaml"}, {})
        self.assertEqual(
            sorted(result.discovered_from),
            ["packs/alpha.yaml", "packs/beta.yaml"],
        )

    def test_the_emitted_set_matches_what_the_resolver_records(self) -> None:
        """The anti-drift pin, and the reason there is no second filter.

        ``resolve_source_files`` decides which paths are in scope for
        incremental hashing and coverage. A *file* it records that this
        strategy does not cover reads as permanently uncovered scope:
        ``coverage_stale`` never clears, and every read re-runs discovery. So
        the two must agree on every path that is a file -- the comparison is
        taken over exactly that set, because the only paths they may differ on
        are the directories a single-directory glob drags in, which are not
        files at all.
        """
        for source in (
            {"glob": "packs/*"},
            {"glob": "packs/*", "exclude": ["packs/*.md"]},
            {"glob": "packs/*.yaml", "files": ["root.toml"]},
            {"glob": "packs/nonexistent/*"},
        ):
            with self.subTest(source=source):
                emitted = {
                    node["props"]["file"]
                    for node in self._extract(source).values()
                }
                resolved = {
                    rel for rel in resolve_source_files(self.root, source)
                    if (self.root / rel).is_file()
                }
                self.assertEqual(
                    emitted, resolved,
                    "the strategy and the resolver disagree about this "
                    "entry's file set",
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
