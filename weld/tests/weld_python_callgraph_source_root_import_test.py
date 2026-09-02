"""An absolute import inside a package binds against the tree's source root.

bd ``z98p7``, field-eval finding M2, designed by ADR 0143. ``src/acme/config.py``
is spelled ``acme.config`` by every import in the tree and ``src.acme.config``
by the id weld derives from its path. Reading the spelling literally minted a
second, speculative symbol under the written name and put the call edges on it,
so ``wd callers`` on the definition -- the node that holds the file -- answered
"no callers" while a node with no file held them.

The rule that fixes it is the one bd ``sigz2`` already shipped for a script
directory, with its predicate generalized: walk up from the importing file's own
directory while ``__init__.py`` is there, and the first ancestor without one is
the ``sys.path`` entry a written name resolves under. This file holds what that
generalization *adds* -- a file inside a package tree, and the two refusals the
wider reach makes necessary. What the degenerate case (source root == the
importer's own directory) resolves and refuses is
``weld_python_callgraph_sibling_import_test`` and its ``_refusals`` sibling,
unchanged: the shipped behaviour is this rule's narrow end, not a second rule.

Every case builds real files and reads what ``python_callgraph.extract``
actually wrote (ADR 0139 mechanism 1). None of them asserts an id *changed*:
ADR 0143 D1 keeps ``symbol:`` ids path-derived, so what moves is which id a
call names, never what a node is called.
"""

from __future__ import annotations

import unittest

from weld.tests._import_table_fixture import ExtractCase, write


class PackageUnderASourceRootTest(ExtractCase):
    """The M2 shape: a package tree under ``src/``, imported by its own name.

    Two of the three branches that read the module slot are exercised on one
    tree -- the from-import lookup and the module-alias attribute call -- which
    is the point of correcting the table rather than the resolver: neither
    branch knows this rule exists.
    """

    RUNNER = "symbol:py:src.acme.sub.runner:run"
    ALIASED = "symbol:py:src.acme.sub.runner:run_aliased"
    DEFINITION = "symbol:py:src.acme.sub.config:load_config"
    IMPORT_SPELLING = "symbol:py:acme.sub.config:load_config"

    def build_tree(self) -> None:
        write(self.tmp, "src/acme/__init__.py", "")
        write(self.tmp, "src/acme/sub/__init__.py", "")
        write(
            self.tmp,
            "src/acme/sub/config.py",
            """
            def load_config():
                return {}
            """,
        )
        write(
            self.tmp,
            "src/acme/sub/runner.py",
            """
            import acme.sub.config as cfg

            from acme.sub.config import load_config


            def run():
                return load_config()

            def run_aliased():
                return cfg.load_config()
            """,
        )

    def test_the_call_lands_on_the_definition_that_holds_the_file(self) -> None:
        nodes, edges = self.run_extract()
        self.assertIn(self.DEFINITION, self.targets(edges, self.RUNNER))
        self.assertEqual(
            nodes[self.DEFINITION]["props"].get("file"), "src/acme/sub/config.py"
        )

    def test_the_module_alias_branch_reads_the_same_corrected_entry(self) -> None:
        _, edges = self.run_extract()
        self.assertIn(self.DEFINITION, self.targets(edges, self.ALIASED))

    def test_no_speculative_twin_is_minted_under_the_written_spelling(self) -> None:
        """One function, one node -- the whole of finding M2.

        Asserted as the absence of the id *and* as a count over the module, so
        a twin under some third spelling would fail here too.
        """
        nodes, _ = self.run_extract()
        self.assertNotIn(self.IMPORT_SPELLING, nodes)
        identities = sorted(
            node_id
            for node_id, node in nodes.items()
            if node.get("label") == "load_config"
        )
        self.assertEqual(identities, [self.DEFINITION])


class RootIsAPackageTest(ExtractCase):
    """The walk stops at the repository root, even when the root is a package.

    A checkout whose own root carries ``__init__.py`` has its ``sys.path`` entry
    *above* the tree, where weld can name nothing. The walk runs out of
    repo-relative segments and returns no prefix, so the written spelling stands
    and the call resolves to a stub -- the pre-existing answer. Probing above the
    root instead would read a directory that is not part of this tree and mint an
    id under a name no node here could ever hold.
    """

    def build_tree(self) -> None:
        write(self.tmp, "__init__.py", "")
        write(self.tmp, "pkg/__init__.py", "")
        write(self.tmp, "pkg/mod.py", "def work():\n    return 1\n")
        write(
            self.tmp,
            "pkg/user.py",
            """
            from mod import work


            def go():
                return work()
            """,
        )

    def test_a_fully_packaged_tree_gets_no_prefix(self) -> None:
        nodes, edges = self.run_extract()
        targets = self.targets(edges, "symbol:py:pkg.user:go")
        self.assertIn("symbol:py:mod:work", targets)
        # ``pkg.mod`` is a real definition here -- it is what the path derives
        # for ``pkg/mod.py`` -- so the claim is that no *call* was pointed at
        # it by a prefix the walk had no segments left to build.
        self.assertNotIn("symbol:py:pkg.mod:work", targets)
        self.assertEqual(
            nodes["symbol:py:pkg.mod:work"]["props"].get("file"), "pkg/mod.py"
        )


class StdlibShadowedUnderASourceRootTest(ExtractCase):
    """The source root is searched before the standard library, one level up too.

    The narrow end of this rule already answers this: ``scripts/json.py`` beside
    its importer wins over the stdlib, and
    ``weld_python_callgraph_sibling_import_test::StdlibShadowedByASiblingTest``
    pins it with "this is the shape that would break if the rule ever grew a
    'leave standard-library names alone' shortcut". One predicate cannot answer
    the same question differently one level up, so the same precedence holds for
    a file inside a package: ``src/`` is the ``sys.path`` entry, ``src/json.py``
    is what ``import json`` binds to there, and weld says so.

    ADR 0143 D3 lists a stdlib refusal among the guards the generalization
    carries over. It is not implemented, deliberately: that bullet restates the
    guard the generalization *removes* -- a bare name inside a package being an
    absolute import, which is exactly why the walk must look above the package
    -- and taking it literally would reverse the shipped contract above while
    the same decision claims the shipped rewrites are made identically.
    """

    def build_tree(self) -> None:
        write(self.tmp, "src/json.py", "def loads(text):\n    return {}\n")
        write(self.tmp, "src/app/__init__.py", "")
        write(
            self.tmp,
            "src/app/reader.py",
            """
            import json


            def read(text):
                return json.loads(text)
            """,
        )

    def test_a_module_under_the_source_root_wins_over_the_stdlib_name(self) -> None:
        nodes, edges = self.run_extract()
        targets = self.targets(edges, "symbol:py:src.app.reader:read")
        self.assertIn("symbol:py:src.json:loads", targets)
        self.assertNotIn("symbol:py:json:loads", nodes)
        self.assertEqual(
            nodes["symbol:py:src.json:loads"]["props"].get("file"), "src/json.py"
        )


class LiteralSpellingWinsTest(ExtractCase):
    """A name this glob already owns is answered literally, not re-prefixed.

    Both readings name a real module here: ``helper.thing`` sits at the root and
    ``src/helper/thing.py`` sits under the source root. The ancestor-relative
    reading is an inference and may only answer a name that resolves to nothing
    otherwise, so the spelling the source wrote -- which the graph can already
    satisfy -- wins. This is also what keeps the rule off an entry
    ``_python_relative_import`` has just resolved: an absolute name is a sibling
    of nothing.
    """

    def build_tree(self) -> None:
        write(self.tmp, "helper/__init__.py", "")
        write(self.tmp, "helper/thing.py", "def work():\n    return 'root'\n")
        write(self.tmp, "src/helper/__init__.py", "")
        write(self.tmp, "src/helper/thing.py", "def work():\n    return 'src'\n")
        write(self.tmp, "src/app/__init__.py", "")
        write(
            self.tmp,
            "src/app/user.py",
            """
            from helper.thing import work


            def go():
                return work()
            """,
        )

    def test_the_written_module_is_kept_when_the_glob_already_owns_it(self) -> None:
        _, edges = self.run_extract()
        targets = self.targets(edges, "symbol:py:src.app.user:go")
        self.assertIn("symbol:py:helper.thing:work", targets)
        self.assertNotIn("symbol:py:src.helper.thing:work", targets)


class SelfImportRefusalTest(ExtractCase):
    """A candidate equal to the importing file's own module is refused.

    A package ``__init__`` that names its own package (``import pkg``, then
    ``pkg.something()``) is the live shape: under the source root the candidate
    ``src.pkg`` *is* this file's module and *is* in the glob, so every check
    before this one passes it. Resolving it would mint a symbol inside the importing
    file's own module for a name that file never defines -- a definite-looking
    id fabricated out of the rule's own arithmetic, which is worse than the
    external stub the literal reading gives.
    """

    def build_tree(self) -> None:
        write(
            self.tmp,
            "src/pkg/__init__.py",
            """
            import pkg


            def boot():
                return pkg.missing()
            """,
        )
        write(self.tmp, "src/pkg/other.py", "def work():\n    return 1\n")

    def test_a_module_never_resolves_an_import_to_itself(self) -> None:
        nodes, edges = self.run_extract()
        targets = self.targets(edges, "symbol:py:src.pkg:boot")
        self.assertNotIn("symbol:py:src.pkg:missing", targets)
        self.assertNotIn("symbol:py:src.pkg:missing", nodes)
        self.assertIn("symbol:py:pkg:missing", targets)


class DottedStemTest(ExtractCase):
    """A file whose stem contains a dot does not skew the prefix it derives.

    ``demo.launch.py`` is named ``...launch.demo.launch``: its dotted path
    carries a segment its directory chain does not, and a prefix derived by
    trimming that path rather than walking the directories would land one level
    too deep and resolve ``import launch`` to the file itself. Walking the
    repo-relative segments in lockstep with the directories keeps the two
    aligned, so the candidate is the honest ``src.launch``, which nothing here
    defines, and the third-party reading stands.
    """

    def build_tree(self) -> None:
        write(self.tmp, "src/demo_pkg/__init__.py", "")
        write(
            self.tmp,
            "src/demo_pkg/demo.launch.py",
            """
            import launch


            def generate():
                return launch.LaunchDescription()
            """,
        )

    def test_a_dotted_stem_does_not_resolve_its_import_to_itself(self) -> None:
        nodes, edges = self.run_extract()
        caller = "symbol:py:src.demo_pkg.demo.launch:generate"
        targets = self.targets(edges, caller)
        self.assertIn("symbol:py:launch:LaunchDescription", targets)
        self.assertNotIn(
            "symbol:py:src.demo_pkg.demo.launch:LaunchDescription", nodes
        )


if __name__ == "__main__":
    unittest.main()
