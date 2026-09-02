"""What the script-relative-import rule declines, and why each refusal earns it.

bd sigz2. The rule that resolves a bare import to the file beside its importer
(``weld.strategies._python_source_root_import``, and what it resolves is
``weld_python_callgraph_sibling_import_test``) is an *inference*: nothing in the
syntax says "sibling", only the interpreter's own ``sys.path[0]`` behaviour
does. So the interesting half is the refusals, and every one below is a shape
live in this repo rather than an invented hazard.

Read against the generalized rule (bd z98p7, ADR 0143), each case below is the
same refusal reached one way or another -- the walk that looks for the
``sys.path`` entry, and the glob bound on what it may name:

* **The importer's directory has no ``__init__.py``.** Beside one, a bare name
  is an absolute import that cannot mean a sibling. ``weld/providers/
  anthropic.py`` opens ``from anthropic import Anthropic`` -- the third-party
  SDK -- and ``weld/strategies/tree_sitter.py`` opens ``import tree_sitter``.
  Each names its own file, so a sibling-first rule resolves the module to
  *itself*: worse than the bug being fixed. Under the generalized rule the walk
  answers this by climbing past the package to the real source root, which for
  both of those files is the repository root, where there is no prefix to add
  and the written name stands. It is still a filesystem probe rather than a
  glob-membership test, because "is this directory a package" must not vary
  with how the glob was written.
* **The candidate is a module the glob owns.** Nine files under ``tools/`` open
  ``import tier_check_grammar_gate``, whose module is ``weld/tests/``'s, reached
  through Bazel runfiles rather than from beside the importer; no glob matches
  it, so no node represents it. Keeping the literal spelling there is the same
  "name a node that is already there, or decline" discipline bd vrdcj's
  class-base rule keeps -- and it is what leaves an ordinary third-party import
  alone.

A module at the repository root is refused for a third reason, which is that it
needs no rewrite: its siblings' dotted paths are already their bare file stems.
"""

from __future__ import annotations

import unittest

from weld.tests._import_table_fixture import ExtractCase, write


class PackageDirectorySiblingTest(ExtractCase):
    """A directory WITH ``__init__.py`` -- where the sibling reading is wrong.

    ``provider/`` is a package and the repository root above it is not, so the
    source root is the root, the prefix is empty, and the written name stands.
    The generalized rule reaches the same refusal by climbing rather than by
    stopping (bd z98p7): had ``provider/`` sat under a source root, the
    candidate would have been that root's ``anthropic``, never this file.
    """

    def build_tree(self) -> None:
        write(self.tmp, "provider/__init__.py", "")
        write(
            self.tmp,
            "provider/anthropic.py",
            """
            from anthropic import Anthropic


            def client():
                return Anthropic()
            """,
        )

    def test_a_package_module_importing_its_own_name_is_not_self_resolved(
        self,
    ) -> None:
        nodes, edges = self.run_extract()
        caller = "symbol:py:provider.anthropic:client"
        self.assertNotIn(
            "symbol:py:provider.anthropic:Anthropic",
            self.targets(edges, caller),
            "inside a package a bare import names the third-party module, so "
            "the file must not resolve the import to itself",
        )
        target = "symbol:py:anthropic:Anthropic"
        self.assertIn(target, self.targets(edges, caller))
        self.assertEqual(nodes[target]["props"].get("origin"), "external")


class UnglobbedSiblingTest(ExtractCase):
    """A module reachable at runtime but matched by no glob keeps its spelling.

    ``GLOB`` is narrowed to one directory so ``elsewhere/shared_gate.py`` is
    real on disk and absent from the graph -- the ``tier_check_grammar_gate``
    shape, where a filesystem-only rule would retarget onto an id no node holds.
    """

    GLOB = "scripts/*.py"

    def build_tree(self) -> None:
        write(self.tmp, "elsewhere/shared_gate.py", "def run():\n    return 1\n")
        write(
            self.tmp,
            "scripts/consumer.py",
            """
            import shared_gate


            def calls_an_unglobbed_module():
                return shared_gate.run()
            """,
        )

    def test_a_sibling_outside_every_glob_is_not_retargeted(self) -> None:
        nodes, edges = self.run_extract()
        caller = "symbol:py:scripts.consumer:calls_an_unglobbed_module"
        self.assertNotIn("symbol:py:scripts.shared_gate:run", nodes)
        self.assertIn("symbol:py:shared_gate:run", self.targets(edges, caller))


class ThirdPartyImportTest(ExtractCase):
    """An ordinary third-party import from a script directory is untouched.

    Both conditions hold except the one that matters: the directory is not a
    package, and there is no ``scripts/yaml.py`` behind the name. Without the
    glob-membership check this would resolve under a ``scripts.yaml`` that does
    not exist -- trading one fabricated population for a larger one.
    """

    def build_tree(self) -> None:
        write(
            self.tmp,
            "scripts/consumer.py",
            """
            from yaml import safe_load


            def parse(text):
                return safe_load(text)
            """,
        )

    def test_a_third_party_import_with_no_sibling_file_is_untouched(self) -> None:
        nodes, edges = self.run_extract()
        target = "symbol:py:yaml:safe_load"
        self.assertIn(
            target, self.targets(edges, "symbol:py:scripts.consumer:parse")
        )
        self.assertNotIn("symbol:py:scripts.yaml:safe_load", nodes)
        self.assertEqual(nodes[target]["props"].get("origin"), "external")


class RootModuleSiblingTest(ExtractCase):
    """At the repository root the source spelling is already the dotted path.

    ``setup.py`` beside ``helper.py`` imports ``helper``, and ``helper.py``'s
    own module path *is* ``helper`` -- no package prefix to add, so the refusal
    costs nothing and the call resolves through the ordinary import-table
    lookup.
    """

    def build_tree(self) -> None:
        write(self.tmp, "helper.py", "def shared():\n    return 1\n")
        write(
            self.tmp,
            "setup.py",
            """
            from helper import shared


            def build():
                return shared()
            """,
        )

    def test_a_root_level_sibling_resolves_without_a_rewrite(self) -> None:
        nodes, edges = self.run_extract()
        target = "symbol:py:helper:shared"
        self.assertIn(target, self.targets(edges, "symbol:py:setup:build"))
        self.assertEqual(nodes[target]["props"].get("origin"), "project")


if __name__ == "__main__":
    unittest.main()
