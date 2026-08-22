"""Tests for the shared referring-strategy target-ID rule.

``weld.strategies._target_ids`` answers one question for the two strategies
that record a relationship to a path they only *read about*
(``validator_targets`` harvests path literals from lint modules,
``concept_from_bd`` harvests them from issue descriptions): which node-ID
spellings might that path have been minted under?

The rule lives in one module because the two strategies used to each carry
their own copy and one rotted through the ADR 0041 ``file:`` rename without
a single test failing -- a wrong spelling and an unresolvable one are
indistinguishable once the dangling-edge sweep has run. These tests are the
pins that make the next such drift loud.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld import _node_ids
from weld._node_ids import file_id
from weld.strategies import _target_ids, config_file, tool_script
from weld.strategies._target_ids import (
    DOC_EXTENSIONS,
    FILE_NODE_EXTENSIONS,
    config_id,
    target_ids,
)


class TargetIdsTest(unittest.TestCase):
    """Every ID class a repo may name the same file under is emitted."""

    def test_covers_canonical_doc_and_config_spellings(self) -> None:
        self.assertEqual(
            ["file:pkg/mod", "config:pkg_mod_py"], target_ids("pkg/mod.py")
        )
        self.assertEqual(
            ["doc:docs/guide", "config:docs_guide_md"],
            target_ids("docs/guide.md"),
        )

    def test_strips_only_the_final_extension(self) -> None:
        self.assertEqual("file:pkg/a.b", target_ids("pkg/a.b.py")[0])

    def test_file_spelling_is_the_canonical_adr_0041_form(self) -> None:
        # The regression that motivated the shared module: the ``file:``
        # candidate must be exactly what ``file_id`` mints, so it matches
        # the node the file-emitting strategy actually created. The two
        # pre-rename spellings must be gone.
        rel = "weld/discover.py"
        self.assertEqual(file_id(rel), target_ids(rel)[0])
        self.assertNotIn("file:weld/discover.py", target_ids(rel))
        self.assertNotIn("file:discover", target_ids(rel))

    def test_non_source_extension_never_offers_a_file_spelling(self) -> None:
        """A .sh literal must not be able to hit a same-stem .py node.

        ``file_id`` strips the final extension, so ``bin/run.sh`` and
        ``bin/run.py`` mint the same ``file:bin/run``. Offering the
        ``file:`` spelling for non-source extensions would land the edge on
        a real but wrong node, which no dangling-edge sweep can catch.
        """
        for rel in ("bin/run.sh", "cfg/app.yaml", "docs/guide.md",
                    "BUILD.bazel", "pkg/data.json", "notes.txt"):
            with self.subTest(rel=rel):
                self.assertNotIn(
                    "file:" + rel.rsplit(".", 1)[0], target_ids(rel),
                )

    def test_pyi_stub_keeps_the_file_spelling(self) -> None:
        self.assertIn("file:pkg/mod", target_ids("pkg/mod.pyi"))

    def test_extension_sets_do_not_overlap(self) -> None:
        # A single path must never be offered both a ``file:`` and a
        # ``doc:`` spelling of the same stem; that would make two ID
        # classes compete for one node.
        self.assertFalse(FILE_NODE_EXTENSIONS & DOC_EXTENSIONS)

    def test_output_is_deterministic_and_ordered(self) -> None:
        # ADR 0012 sec. 3: caller edge order must depend on the path alone.
        for rel in ("pkg/mod.py", "docs/guide.md", "notes.txt"):
            with self.subTest(rel=rel):
                self.assertEqual(target_ids(rel), target_ids(rel))

    def test_config_spelling_is_always_offered(self) -> None:
        # The config_file strategy may claim any path the deploying repo
        # lists, so the ``config:`` candidate has no extension gate.
        for rel in ("CLAUDE.md", "MODULE.bazel", "pkg/mod.py"):
            with self.subTest(rel=rel):
                self.assertIn(config_id(rel), target_ids(rel))


class ToolSpellingTest(unittest.TestCase):
    """The ``tool:`` candidate, and why it could not be offered before.

    This module's docstring claimed since bd hxsi that shell scripts reach
    the graph as ``tool:`` "which the other spellings below cover" -- and no
    code path emitted one. Every ``validates`` edge ``validator_targets``
    aimed at a shell script therefore fell back to the ``config:`` spelling
    and dangled, leaving nothing in the graph that joined a validator to the
    script it governs (bd mdvp).

    The blocker was not the missing list entry. ``tool:`` IDs were bare
    stems, so a referrer offering ``tool:publish`` for ``tools/publish.sh``
    could land its edge on ``scripts/publish.sh`` -- a real node, therefore
    invisible to the dangling-edge sweep, which is exactly the hazard
    :data:`FILE_NODE_EXTENSIONS` is deny-by-default to prevent.
    :func:`weld._node_ids.tool_id` path-qualifies the ID, removing the
    collision by construction rather than narrowing the odds of it.
    """

    def test_shell_literal_offers_the_tool_spelling(self) -> None:
        self.assertIn("tool:tools/publish", target_ids("tools/publish.sh"))

    def test_extensionless_literal_offers_the_tool_spelling(self) -> None:
        # The class ``tool_script`` exists to classify, and the one no
        # suffix rule can reach.
        self.assertEqual(
            ["tool:gradlew", "config:gradlew"],
            target_ids("gradlew"),
        )

    def test_the_offered_spelling_is_path_qualified(self) -> None:
        # The property that made the offer safe. Two same-stem scripts in
        # different directories must be offered different candidates.
        self.assertNotEqual(
            target_ids("tools/publish.sh"), target_ids("scripts/publish.sh")
        )

    def test_python_literal_does_not_offer_a_tool_spelling(self) -> None:
        # A ``.py`` path is claimed by ``python_module`` as ``file:``. Adding
        # a ``tool:`` candidate for it would be a second guess where the
        # first one is already right.
        self.assertNotIn("tool:pkg/mod", target_ids("pkg/mod.py"))

    def test_referrer_reexports_the_tool_minting_authority(self) -> None:
        self.assertIs(_node_ids.tool_id, _target_ids.tool_id)

    def test_tool_script_mints_what_the_referrer_offers(self) -> None:
        # The end-to-end property mdvp was filed on: the edge a referring
        # strategy guesses at must land on the node ``tool_script``
        # actually minted. Silent by construction when it drifts.
        entries = ["install.sh", "tools/publish.sh", "gradlew"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tools").mkdir()
            for rel in entries:
                (root / rel).write_text("#!/bin/sh\n", encoding="utf-8")
            minted = {}
            for glob in ("*.sh", "tools/**/*.sh", "gradlew"):
                minted.update(tool_script.extract(root, {"glob": glob}, {}).nodes)

        self.assertEqual(len(minted), len(entries))
        for rel in entries:
            with self.subTest(rel=rel):
                offered = target_ids(rel)
                self.assertTrue(
                    any(nid in offered for nid in minted),
                    f"{rel}: minted {sorted(minted)}, offered {offered}",
                )


class ConfigIdTest(unittest.TestCase):
    """The one ``config:`` spelling rule, in weld._node_ids (ADR 0041)."""

    def test_dots_and_separators_collapse_to_underscores(self) -> None:
        self.assertEqual("config:docs_guide_md", config_id("docs/guide.md"))

    def test_leading_dot_is_stripped(self) -> None:
        self.assertEqual("config:bazelrc", config_id(".bazelrc"))

    def test_referrer_reexports_the_minting_authority(self) -> None:
        # Not a copy of the rule: the same function object. bd hxsi moved it
        # out of this module, because a guesser holding the authoritative
        # copy of a minting rule is the inversion that let the previous
        # duplicate rot (bd u5dt).
        self.assertIs(_node_ids.config_id, _target_ids.config_id)


class MinterAndReferrerAgreeTest(unittest.TestCase):
    """The strategy that *creates* config nodes spells them the same way.

    The failure this pins is silent by construction: a wrong spelling and an
    unresolvable one are indistinguishable once
    ``_discover_postprocess._clean_and_dedup_edges`` has dropped the dangling
    edge, so a drift here costs the referring strategies their edges without
    failing anything (bd hxsi, the same shape as bd u5dt).
    """

    def test_extracted_node_ids_match_config_id(self) -> None:
        entries = [".bazelrc", "MODULE.bazel", "docs/guide.md"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            for rel in entries:
                (root / rel).write_text("", encoding="utf-8")
            result = config_file.extract(root, {"files": entries}, {})

        self.assertEqual(
            sorted(config_id(rel) for rel in entries), sorted(result.nodes)
        )

    def test_every_minted_id_is_offered_by_the_referrer(self) -> None:
        # The end-to-end property: an edge a referring strategy guesses at
        # actually lands on the node config_file minted.
        entries = ["pyproject.toml", "docs/guide.md"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            for rel in entries:
                (root / rel).write_text("", encoding="utf-8")
            result = config_file.extract(root, {"files": entries}, {})

        for rel in entries:
            with self.subTest(rel=rel):
                offered = target_ids(rel)
                self.assertTrue(
                    any(nid in offered for nid in result.nodes),
                    f"{rel}: minted {sorted(result.nodes)}, offered {offered}",
                )


if __name__ == "__main__":
    unittest.main()
