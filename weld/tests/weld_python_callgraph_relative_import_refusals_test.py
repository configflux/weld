"""What the relative-import rule declines, and why a refusal is the right answer.

bd ``zr486``. The resolutions are
``weld_python_callgraph_relative_import_test``; this file holds the two shapes
where the arithmetic has no answer, and the point of both is that "no answer" is
not a fallback here -- it is the correct answer.

Neither refusal is a policy this repo chose. Both are CPython's own
``importlib._bootstrap`` sanity checks, transcribed:

* ``from .. import x`` in a top-level package's module is "attempted relative
  import beyond top-level package". Producing an answer would mean naming a
  package above the root -- inventing exactly the kind of id this change
  removes, one directory further out.
* ``from .helper import x`` in a module under no package at all is "attempted
  relative import with no known parent package". There is no dotted prefix a
  rewrite could use.

Both fall to the ``symbol:unresolved:`` sentinel, and that is the deliberate
trade the strategy states in rule 3: a miss is recoverable and visible, while a
confident wrong answer is what a reader acts on. The assertions below are
therefore in two halves -- the sentinel *is* reached, and no fabricated
``symbol:py:`` id is minted alongside it.
"""

from __future__ import annotations

import unittest

from weld.tests._import_table_fixture import ExtractCase, write


class BeyondTopLevelPackageTest(ExtractCase):
    """A level that walks past the root package resolves to nothing."""

    CALLER = "symbol:py:pkg.caller:calls_beyond"

    def build_tree(self) -> None:
        write(self.tmp, "pkg/__init__.py", "")
        write(self.tmp, "pkg/helper.py", "def work():\n    return 1\n")
        write(
            self.tmp,
            "pkg/caller.py",
            """
            from ..helper import work


            def calls_beyond():
                return work()
            """,
        )

    def test_the_call_reaches_the_sentinel(self) -> None:
        _nodes, edges = self.run_extract()
        self.assertIn("symbol:unresolved:work", self.targets(edges, self.CALLER))

    def test_nothing_above_the_root_is_minted(self) -> None:
        """The refusal exists so no id names a package that cannot exist."""
        nodes, edges = self.run_extract()
        targets = {e["to"] for e in edges}
        for fabricated in ("symbol:py:helper:work", "symbol:py:.helper:work"):
            self.assertNotIn(fabricated, nodes)
            self.assertNotIn(fabricated, targets)

    def test_the_real_sibling_is_not_borrowed_instead(self) -> None:
        """``pkg.helper`` exists and is the tempting wrong answer.

        Level 2 does not mean level 1, and a rule that fell back to the nearest
        module that happens to resolve would be the confident-wrong-answer
        failure this change is about, wearing a plausible id.
        """
        _nodes, edges = self.run_extract()
        self.assertNotIn(
            "symbol:py:pkg.helper:work", self.targets(edges, self.CALLER)
        )


class NoParentPackageTest(ExtractCase):
    """A module at the tree root has no package for a relative import to use."""

    CALLER = "symbol:py:caller:calls_relative"

    def build_tree(self) -> None:
        write(self.tmp, "helper.py", "def work():\n    return 1\n")
        write(
            self.tmp,
            "caller.py",
            """
            from .helper import work


            def calls_relative():
                return work()
            """,
        )

    def test_the_call_reaches_the_sentinel(self) -> None:
        _nodes, edges = self.run_extract()
        self.assertIn("symbol:unresolved:work", self.targets(edges, self.CALLER))

    def test_the_root_level_module_is_not_borrowed_instead(self) -> None:
        """``helper.py`` sits right there, and is still not what was asked for.

        Reading the source's spelling literally -- the old behaviour -- lands on
        ``symbol:py:helper:work`` here and looks *right*, because at the root the
        fabricated name and a real one coincide. That coincidence is why the
        refusal is asserted rather than assumed: it is the shape in which a
        wrong rule passes review.
        """
        _nodes, edges = self.run_extract()
        self.assertNotIn(
            "symbol:py:helper:work", self.targets(edges, self.CALLER)
        )


class AbsoluteImportsAreUntouchedTest(ExtractCase):
    """``level == 0`` still means what the source says, in both spellings.

    The regression guard on the change itself: the level branch must not capture
    an ordinary absolute import, and a third-party name with no file behind it
    must keep its literal spelling rather than acquiring a package prefix.
    """

    def build_tree(self) -> None:
        write(self.tmp, "pkg/__init__.py", "")
        write(self.tmp, "pkg/helper.py", "def work():\n    return 1\n")
        write(
            self.tmp,
            "pkg/consumer.py",
            """
            import json

            from pkg.helper import work
            from yaml import safe_load


            def calls_absolute_first_party():
                return work()

            def calls_a_third_party():
                return safe_load("{}")

            def calls_stdlib_by_alias():
                return json.loads("{}")
            """,
        )

    def test_an_absolute_first_party_import_still_resolves(self) -> None:
        _nodes, edges = self.run_extract()
        self.assertIn(
            "symbol:py:pkg.helper:work",
            self.targets(edges, "symbol:py:pkg.consumer:calls_absolute_first_party"),
        )

    def test_a_third_party_name_keeps_its_literal_module(self) -> None:
        nodes, edges = self.run_extract()
        target = "symbol:py:yaml:safe_load"
        self.assertIn(
            target,
            self.targets(edges, "symbol:py:pkg.consumer:calls_a_third_party"),
        )
        self.assertEqual(nodes[target]["props"].get("origin"), "external")
        self.assertNotIn("symbol:py:pkg.yaml:safe_load", nodes)

    def test_a_stdlib_module_alias_keeps_its_literal_module(self) -> None:
        nodes, edges = self.run_extract()
        self.assertIn(
            "symbol:py:json:loads",
            self.targets(edges, "symbol:py:pkg.consumer:calls_stdlib_by_alias"),
        )
        self.assertNotIn("symbol:py:pkg.json:loads", nodes)


if __name__ == "__main__":  # pragma: no cover - bazel runs unittest directly
    unittest.main()
