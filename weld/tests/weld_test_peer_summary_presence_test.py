"""bd cw4f (ADR 0125 follow-up): the
"always present, empty when absent" shape of ``props.summary`` across every
test_peer language, including the one that deliberately still has no
reader.

Split out of weld_test_peer_multilang_test.py to keep that file under the
line-count cap. Dependency-free like its sibling (no real tree-sitter parse
-- the fixture files below carry no leading comment at all), so this only
pins the *shape* of the contract: the key exists on every language's node
regardless of whether a reader is registered for it.
weld_test_peer_file_summary_test.py and
weld_test_peer_java_file_summary_test.py pin actual comment extraction (a
real leading comment producing a non-empty summary) for the five languages
that do have a reader.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.strategies._helpers import StrategyResult
from weld.strategies.test_peer import _SUMMARY_RESOLVERS_BY_SUFFIX, extract


def _touch(path: Path, content: str = "") -> None:
    """Create *path* with *content*, creating parents as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestSummaryPropIsAlwaysPresent(unittest.TestCase):
    """Every language's node carries ``props.summary`` -- ``""`` for a
    language with no reader (``.cs``) or a file with no leading comment,
    non-empty content for the five with a reader (proven elsewhere, see
    module docstring)."""

    def tearDown(self) -> None:
        if hasattr(self, "_tmp"):
            self._tmp.cleanup()

    def _make_tree(self, files: list[str]) -> Path:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        for rel in files:
            _touch(root / rel, "x\n")
        return root

    def _run(self, root: Path, glob: str) -> StrategyResult:
        return extract(root, {"glob": glob}, {})

    #: ``(language, files, glob, node_id)`` -- one entry per test_peer
    #: language, C# included, so the table itself documents which five
    #: have a reader and which one (still) does not.
    _CASES: tuple[tuple[str, list[str], str, str], ...] = (
        ("python", ["lib/thing.py", "lib/tests/thing_test.py"],
         "lib/tests/*_test.py", "file:lib/tests/thing_test"),
        ("go", ["pkg/foo.go", "pkg/foo_test.go"],
         "**/*_test.go", "file:pkg/foo_test"),
        ("typescript", ["src/foo.ts", "src/foo.test.ts"],
         "src/*.test.ts", "file:src/foo.test"),
        ("java", ["pkg/Foo.java", "pkg/FooTest.java"],
         "pkg/*Test.java", "file:pkg/FooTest"),
        ("csharp", ["pkg/Foo.cs", "pkg/FooTests.cs"],
         "pkg/*Tests.cs", "file:pkg/FooTests"),
        ("rust", ["src/integration.rs", "tests/integration.rs"],
         "tests/*.rs", "file:tests/integration"),
    )

    def test_summary_key_present_for_every_language(self) -> None:
        for language, files, glob, node_id in self._CASES:
            with self.subTest(language=language):
                root = self._make_tree(files)
                try:
                    node = self._run(root, glob).nodes[node_id]
                    self.assertIn("summary", node["props"])
                    # No fixture file here has a real leading comment (or,
                    # for csharp, any reader at all), so every case is
                    # empty -- the point is the KEY's unconditional
                    # presence, not its content.
                    self.assertEqual(node["props"]["summary"], "")
                finally:
                    self._tmp.cleanup()

    def test_csharp_has_no_summary_resolver_registered(self) -> None:
        """Structural pin for the ADR 0125/7ui6 scope decision: C# is
        deliberately absent from the dispatch table, not silently mapped
        to a no-op reader."""
        self.assertNotIn(".cs", _SUMMARY_RESOLVERS_BY_SUFFIX)
        for suffix in (".py", ".go", ".rs", ".ts", ".tsx", ".js", ".jsx", ".java"):
            self.assertIn(suffix, _SUMMARY_RESOLVERS_BY_SUFFIX)


if __name__ == "__main__":
    unittest.main()
