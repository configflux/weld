"""Mock-patch target harvesting for the Python test_peer helper (bd ymso).

``patch("weld._mcp_sdk.installed_version")`` names a real dependency that no
import records, so before this the graph could not answer "which tests touch
this symbol" for any mock-based test. These tests pin both halves of the
contract:

1. Every recognised patch spelling is found (decorator, context manager,
   ``mock.patch``, ``unittest.mock.patch``), and the non-string entry points
   (``patch.object`` / ``patch.dict``) are not.
2. A target is emitted **only** when it provably names a symbol
   ``python_callgraph`` mints. Every drop case is asserted individually,
   because the failure that matters here is not a missing edge -- it is an
   edge to a wrong-but-real symbol, which the graph would then repeat.

The import-following case (a name mocked where it was re-bound rather than
where it was defined) is the shape that produced bd kj4z, so it gets its own
class.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.contract import VALID_EDGE_TYPES
from weld.strategies import _mock_patch_python as mock_patch
from weld.strategies import _mock_patch_resolve as resolver
from weld.strategies.test_peer import extract


def _touch(path: Path, content: str = "") -> None:
    """Create *path* with *content*, creating parents as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class _TreeCase(unittest.TestCase):
    """A temp project whose modules exercise every resolution branch."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.cache = resolver.new_cache()
        _touch(self.root / "pkg" / "__init__.py")
        _touch(
            self.root / "pkg" / "core.py",
            "def thing():\n    return 1\n\n\nclass Holder:\n"
            "    def method(self):\n        return 2\n",
        )
        _touch(
            self.root / "pkg" / "consumer.py",
            "import os\n"
            "from pkg.core import thing\n"
            "from pkg.core import thing as aliased\n"
            "\n\ndef use():\n    return thing(), os.replace, aliased\n",
        )
        _touch(self.root / "pkg" / "sub" / "__init__.py", "def packaged():\n    return 3\n")

    def resolve(self, dotted: str) -> str | None:
        return resolver.resolve_patch_target(self.root, dotted, self.cache)


class TestResolvesDefinedSymbols(_TreeCase):
    """A target that names a real definition resolves to its symbol id."""

    def test_module_level_function(self) -> None:
        self.assertEqual(self.resolve("pkg.core.thing"), "symbol:py:pkg.core:thing")

    def test_nested_qualname(self) -> None:
        """Methods resolve under the ``Class.method`` qualname callgraph mints."""
        self.assertEqual(
            self.resolve("pkg.core.Holder.method"),
            "symbol:py:pkg.core:Holder.method",
        )

    def test_class_itself(self) -> None:
        self.assertEqual(self.resolve("pkg.core.Holder"), "symbol:py:pkg.core:Holder")

    def test_package_dunder_init(self) -> None:
        """``pkg/sub/__init__.py`` resolves under the package's dotted name."""
        self.assertEqual(
            self.resolve("pkg.sub.packaged"), "symbol:py:pkg.sub:packaged"
        )

    def test_longest_module_prefix_wins(self) -> None:
        """``pkg.core`` beats ``pkg`` when both could host the name."""
        _touch(self.root / "pkg" / "__init__.py", "def thing():\n    return 0\n")
        self.assertEqual(
            resolver.resolve_patch_target(
                self.root, "pkg.core.thing", resolver.new_cache()
            ),
            "symbol:py:pkg.core:thing",
        )


class TestFollowsImportRebinding(_TreeCase):
    """A name mocked where it was re-bound resolves to where it was defined.

    This is the bd kj4z shape: the consumer bound the name into its own
    namespace at import, the test patched it at the consumer, and nothing
    connected either side to the definition.
    """

    def test_imported_name_resolves_to_defining_module(self) -> None:
        self.assertEqual(
            self.resolve("pkg.consumer.thing"), "symbol:py:pkg.core:thing"
        )

    def test_import_alias_resolves_to_original_name(self) -> None:
        self.assertEqual(
            self.resolve("pkg.consumer.aliased"), "symbol:py:pkg.core:thing"
        )

    def test_relative_import_is_declined(self) -> None:
        """``from .core import thing`` names a module ``ast`` cannot resolve.

        The table would record the defining module as ``core``; a project
        with a top-level ``core.py`` would then resolve it to the wrong real
        symbol. Declining is the only honest answer.
        """
        _touch(self.root / "core.py", "def thing():\n    return 9\n")
        _touch(
            self.root / "pkg" / "relative.py",
            "from .core import thing\n\n\ndef use():\n    return thing()\n",
        )
        self.assertIsNone(self.resolve("pkg.relative.thing"))

    def test_reexport_cycle_terminates(self) -> None:
        """Mutually-importing modules must not spin the resolver."""
        _touch(self.root / "pkg" / "ping.py", "from pkg.pong import x\n")
        _touch(self.root / "pkg" / "pong.py", "from pkg.ping import x\n")
        self.assertIsNone(self.resolve("pkg.ping.x"))


class TestDropsUnprovableTargets(_TreeCase):
    """Everything that does not name a project symbol yields no edge."""

    def test_stdlib_target(self) -> None:
        for dotted in ("sys.stdout", "shutil.which", "pathlib.Path.glob"):
            with self.subTest(dotted=dotted):
                self.assertIsNone(self.resolve(dotted))

    def test_imported_module_is_not_a_symbol(self) -> None:
        """``pkg.consumer.os`` names an imported module, which defines nothing."""
        self.assertIsNone(self.resolve("pkg.consumer.os"))

    def test_attribute_of_imported_module(self) -> None:
        """``pkg.consumer.os.replace`` follows to stdlib, which is not source."""
        self.assertIsNone(self.resolve("pkg.consumer.os.replace"))

    def test_undefined_name_in_real_module(self) -> None:
        self.assertIsNone(self.resolve("pkg.core.nope"))

    def test_unknown_module(self) -> None:
        self.assertIsNone(self.resolve("nowhere.at.all"))

    def test_single_segment(self) -> None:
        self.assertIsNone(self.resolve("thing"))

    def test_non_identifier_strings(self) -> None:
        """A URL or route passed to an unrelated ``.patch()`` is not a target."""
        for dotted in ("/api/v1/thing", "https://host/path", "a..b", "1.2"):
            with self.subTest(dotted=dotted):
                self.assertIsNone(self.resolve(dotted))

    def test_unparseable_target_module(self) -> None:
        _touch(self.root / "pkg" / "broken.py", "def (:\n")
        self.assertIsNone(self.resolve("pkg.broken.thing"))


class TestPatchCallRecognition(unittest.TestCase):
    """Which call shapes count as a patch, and which deliberately do not."""

    def targets(self, source: str) -> list[str]:
        return [t for t, _line in mock_patch.patch_targets(source, "<test>")]

    def test_every_spelling_is_found(self) -> None:
        source = (
            'import unittest.mock\n'
            'from unittest import mock\n'
            'from unittest.mock import patch\n'
            '\n'
            '@patch("a.decorated")\n'
            'def test_one(m):\n'
            '    with patch("a.ctx"):\n'
            '        pass\n'
            '    with mock.patch("a.dotted"):\n'
            '        pass\n'
            '    with unittest.mock.patch("a.fully_qualified"):\n'
            '        pass\n'
        )
        self.assertEqual(
            self.targets(source),
            ["a.decorated", "a.ctx", "a.dotted", "a.fully_qualified"],
        )

    def test_object_and_dict_forms_are_ignored(self) -> None:
        """Their first argument is an object or mapping, not a string target."""
        source = (
            'patch.object(mod, "name")\n'
            'patch.dict("os.environ", {})\n'
            'patch.multiple(mod, a=1)\n'
        )
        self.assertEqual(self.targets(source), [])

    def test_non_literal_target_is_ignored(self) -> None:
        source = 'target = "a.b"\npatch(target)\npatch(f"a.{name}")\n'
        self.assertEqual(self.targets(source), [])

    def test_no_args_does_not_raise(self) -> None:
        self.assertEqual(self.targets("patch()\n"), [])

    def test_syntax_error_yields_nothing(self) -> None:
        self.assertEqual(self.targets("def (:\n"), [])

    def test_line_numbers_are_reported(self) -> None:
        self.assertEqual(
            mock_patch.patch_targets('\n\npatch("a.b")\n', "<test>"),
            [("a.b", 3)],
        )


class TestEdgeEmission(_TreeCase):
    """``patch_target_edges`` output shape, read directly off the helper."""

    def _write_test(self, body: str) -> Path:
        rel = Path("tests") / "thing_test.py"
        _touch(self.root / rel, body)
        return rel

    def test_edge_shape(self) -> None:
        rel = self._write_test('from unittest.mock import patch\npatch("pkg.core.thing")\n')
        edges = mock_patch.patch_target_edges(
            self.root, rel, "file:tests/thing_test", cache=self.cache
        )
        self.assertEqual(len(edges), 1)
        edge = edges[0]
        self.assertEqual(edge["from"], "file:tests/thing_test")
        self.assertEqual(edge["to"], "symbol:py:pkg.core:thing")
        self.assertEqual(edge["type"], "depends_on")
        self.assertIn(edge["type"], VALID_EDGE_TYPES)
        props = edge["props"]
        self.assertEqual(props["source_strategy"], "test_peer")
        self.assertEqual(props["confidence"], "definite")
        self.assertEqual(props["resolution"], mock_patch.MOCK_PATCH_RESOLUTION)
        self.assertEqual(props["raw"], "pkg.core.thing")
        self.assertTrue(props["resolved"])

    def test_provenance_is_the_test_file(self) -> None:
        """ADR 0074: the stamp is the file whose scan produced the edge.

        Stamping the patched module instead would be exactly backwards --
        the incremental purge keeps an edge across a node purge only when it
        can attribute it to a file, and the target is the file that is stale
        in precisely the case that must not lose the edge.
        """
        rel = self._write_test('patch("pkg.core.thing")\n')
        edge = mock_patch.patch_target_edges(
            self.root, rel, "file:tests/thing_test", cache=self.cache
        )[0]
        self.assertEqual(edge["props"]["provenance"]["file"], "tests/thing_test.py")

    def test_raw_keeps_the_lookup_path_when_it_differs(self) -> None:
        """``raw`` vs ``to`` disagreeing is the readable re-binding signal."""
        rel = self._write_test('patch("pkg.consumer.thing")\n')
        edge = mock_patch.patch_target_edges(
            self.root, rel, "file:tests/thing_test", cache=self.cache
        )[0]
        self.assertEqual(edge["props"]["raw"], "pkg.consumer.thing")
        self.assertEqual(edge["to"], "symbol:py:pkg.core:thing")

    def test_repeated_target_emits_one_edge(self) -> None:
        rel = self._write_test(
            'patch("pkg.core.thing")\npatch("pkg.core.thing")\n'
            'patch("pkg.consumer.thing")\n'
        )
        edges = mock_patch.patch_target_edges(
            self.root, rel, "file:tests/thing_test", cache=self.cache
        )
        self.assertEqual([e["to"] for e in edges], ["symbol:py:pkg.core:thing"])

    def test_vanished_file_yields_no_edges(self) -> None:
        """A file gone between the walk and the read costs its edges, not the run."""
        edges = mock_patch.patch_target_edges(
            self.root, Path("tests") / "gone_test.py",
            "file:tests/gone_test", cache=self.cache,
        )
        self.assertEqual(edges, [])


class TestThroughTestPeerStrategy(_TreeCase):
    """End-to-end through ``test_peer.extract``, the only caller."""

    def _extract(self, glob: str = "tests/*_test.py") -> object:
        return extract(self.root, {"glob": glob, "type": "file"}, {})

    def test_edge_is_emitted_alongside_the_peer_edge(self) -> None:
        _touch(self.root / "tests" / "core_test.py", 'patch("pkg.core.thing")\n')
        _touch(self.root / "tests" / "core.py", "")
        result = self._extract()
        by_type = {e["type"] for e in result.edges}
        self.assertIn("depends_on", by_type)
        mock_edges = [e for e in result.edges if e["type"] == "depends_on"]
        self.assertEqual(
            [(e["from"], e["to"]) for e in mock_edges],
            [("file:tests/core_test", "symbol:py:pkg.core:thing")],
        )

    def test_peer_edge_is_unaffected(self) -> None:
        _touch(self.root / "tests" / "core.py", "")
        _touch(self.root / "tests" / "core_test.py", 'patch("pkg.core.thing")\n')
        result = self._extract()
        peer = [e for e in result.edges if e["type"] == "tests"]
        self.assertEqual(len(peer), 1)
        self.assertEqual(peer[0]["from"], "file:tests/core_test")

    def test_unresolvable_target_emits_no_edge(self) -> None:
        """The strategy's own output is clean, not merely prunable later."""
        _touch(
            self.root / "tests" / "core_test.py",
            'patch("sys.stdout")\npatch("nowhere.thing")\npatch("pkg.core.nope")\n',
        )
        result = self._extract()
        self.assertEqual([e for e in result.edges if e["type"] == "depends_on"], [])

    def test_non_python_test_file_is_untouched(self) -> None:
        """Mock-patch harvesting is a Python-only concept."""
        _touch(self.root / "tests" / "thing_test.go", 'patch("pkg.core.thing")\n')
        result = self._extract("tests/*_test.go")
        self.assertEqual([e for e in result.edges if e["type"] == "depends_on"], [])

    def test_unparseable_test_file_still_yields_its_node(self) -> None:
        _touch(self.root / "tests" / "core_test.py", "def (:\n")
        result = self._extract()
        self.assertIn("file:tests/core_test", result.nodes)
        self.assertEqual([e for e in result.edges if e["type"] == "depends_on"], [])

    def test_extract_is_deterministic(self) -> None:
        _touch(self.root / "tests" / "core_test.py", 'patch("pkg.core.thing")\n')
        _touch(self.root / "tests" / "other_test.py", 'patch("pkg.consumer.thing")\n')
        first = self._extract()
        second = self._extract()
        self.assertEqual(first.edges, second.edges)
        self.assertEqual(first.discovered_from, second.discovered_from)


if __name__ == "__main__":
    unittest.main()
