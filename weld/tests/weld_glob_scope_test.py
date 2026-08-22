"""Tests for the operation-scoped glob memo (bd cjij).

A discovery run asks :func:`weld.glob_match.walk_glob` for the identical
``(root, pattern, excludes)`` triple several times: once when the source-file
resolver builds the file map, then once more inside every strategy that
re-resolves the same glob in its own ``extract()``.
``_source_resolve.resolve_source_file_map`` already memoizes the first layer
(bd 85tb.2), but it cannot reach inside a strategy -- so on this repo the three
sources on ``weld/**/*.py`` made it four traversals per warm refresh, each one
re-walking the whole tree and re-running the per-path repo-boundary filter for
a result that could not have changed.

These tests pin the replacement contract:

* inside a :func:`weld.glob_match.glob_scope` each distinct triple is walked
  exactly once and repeats are served from that operation's memo;
* the memo is keyed on the full triple, so differing excludes, patterns or
  roots never share a result;
* outside a scope nothing is memoized, so a direct caller is unaffected;
* the memo never outlives its scope -- the next operation observes the tree as
  it is then, the same rule the repo-boundary snapshot follows (bd jbpb);
* nested scopes join the enclosing one rather than starting a fresh memo;
* callers get a copy, so sorting or extending the returned list in place
  cannot corrupt what a later hit serves.
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from weld import glob_match
from weld.glob_match import glob_scope, walk_glob


class GlobScopeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "a.py").write_text("a = 1\n", encoding="utf-8")
        (self.root / "pkg" / "b.py").write_text("b = 2\n", encoding="utf-8")
        (self.root / "top.py").write_text("t = 3\n", encoding="utf-8")
        (self.root / "notes.md").write_text("# notes\n", encoding="utf-8")

    def _count_walks(self):
        """Patch the real walker with a counting passthrough."""
        return mock.patch.object(
            glob_match,
            "_walk_glob_uncached",
            wraps=glob_match._walk_glob_uncached,
        )

    @staticmethod
    def _names(paths) -> list[str]:
        return sorted(p.name for p in paths)

    def test_repeat_identical_request_walks_once_in_scope(self) -> None:
        with self._count_walks() as walker, glob_scope():
            first = walk_glob(self.root, "**/*.py")
            second = walk_glob(self.root, "**/*.py")
            third = walk_glob(self.root, "**/*.py")

        self.assertEqual(walker.call_count, 1, "repeats must not re-walk")
        self.assertEqual(self._names(first), ["a.py", "b.py", "top.py"])
        self.assertEqual(self._names(second), self._names(first))
        self.assertEqual(self._names(third), self._names(first))

    def test_without_scope_every_request_walks(self) -> None:
        with self._count_walks() as walker:
            walk_glob(self.root, "**/*.py")
            walk_glob(self.root, "**/*.py")

        self.assertEqual(walker.call_count, 2, "no scope => no memo")

    def test_memoized_result_matches_unmemoized(self) -> None:
        plain = walk_glob(self.root, "**/*.py")
        with glob_scope():
            walk_glob(self.root, "**/*.py")  # prime
            memoized = walk_glob(self.root, "**/*.py")

        self.assertEqual(sorted(memoized), sorted(plain))

    def test_differing_excludes_are_not_shared(self) -> None:
        with self._count_walks() as walker, glob_scope():
            everything = walk_glob(self.root, "**/*.py")
            pruned = walk_glob(self.root, "**/*.py", excludes=["pkg/**"])

        self.assertEqual(walker.call_count, 2, "excludes are part of the key")
        self.assertEqual(self._names(everything), ["a.py", "b.py", "top.py"])
        self.assertEqual(self._names(pruned), ["top.py"])

    def test_empty_and_absent_excludes_share_one_walk(self) -> None:
        # ``excludes=[]``/``None``/``[""]`` all normalize to "no excludes";
        # they must not each pay a traversal.
        with self._count_walks() as walker, glob_scope():
            walk_glob(self.root, "**/*.py")
            walk_glob(self.root, "**/*.py", excludes=[])
            walk_glob(self.root, "**/*.py", excludes=[""])

        self.assertEqual(walker.call_count, 1)

    def test_differing_patterns_are_not_shared(self) -> None:
        with self._count_walks() as walker, glob_scope():
            py = walk_glob(self.root, "**/*.py")
            md = walk_glob(self.root, "**/*.md")

        self.assertEqual(walker.call_count, 2)
        self.assertEqual(self._names(py), ["a.py", "b.py", "top.py"])
        self.assertEqual(self._names(md), ["notes.md"])

    def test_differing_roots_are_not_shared(self) -> None:
        other = self.root / "pkg"
        with self._count_walks() as walker, glob_scope():
            at_root = walk_glob(self.root, "**/*.py")
            at_pkg = walk_glob(other, "**/*.py")

        self.assertEqual(walker.call_count, 2, "root is part of the key")
        self.assertEqual(self._names(at_root), ["a.py", "b.py", "top.py"])
        self.assertEqual(self._names(at_pkg), ["a.py", "b.py"])

    def test_memo_does_not_outlive_its_scope(self) -> None:
        with glob_scope():
            before = walk_glob(self.root, "**/*.py")
        self.assertEqual(self._names(before), ["a.py", "b.py", "top.py"])

        (self.root / "pkg" / "c.py").write_text("c = 4\n", encoding="utf-8")

        # A long-lived host runs many operations; the next one must observe
        # the tree as it is then, not as the previous operation saw it.
        with self._count_walks() as walker, glob_scope():
            after = walk_glob(self.root, "**/*.py")

        self.assertEqual(walker.call_count, 1)
        self.assertEqual(self._names(after), ["a.py", "b.py", "c.py", "top.py"])

    def test_nested_scope_joins_enclosing(self) -> None:
        with self._count_walks() as walker, glob_scope():
            walk_glob(self.root, "**/*.py")
            with glob_scope():
                walk_glob(self.root, "**/*.py")
            walk_glob(self.root, "**/*.py")

        self.assertEqual(
            walker.call_count, 1, "a nested scope must not restart the memo"
        )

    def test_inner_scope_exit_does_not_drop_outer_memo(self) -> None:
        with glob_scope():
            with glob_scope():
                walk_glob(self.root, "**/*.py")
            with self._count_walks() as walker:
                walk_glob(self.root, "**/*.py")

        self.assertEqual(walker.call_count, 0, "outer memo survives the inner exit")

    def test_returned_list_is_a_copy(self) -> None:
        with glob_scope():
            first = walk_glob(self.root, "**/*.py")
            first.clear()
            first.append(Path("bogus.py"))
            second = walk_glob(self.root, "**/*.py")

        self.assertEqual(self._names(second), ["a.py", "b.py", "top.py"])

    def test_empty_result_is_memoized(self) -> None:
        """A glob matching nothing must be memoized like any other.

        The memo tests ``is None``, not falsiness -- keyed on emptiness, the
        patterns with nothing to find would be the ones re-walking every time.
        """
        with self._count_walks() as walker, glob_scope():
            first = walk_glob(self.root, "**/*.rs")
            second = walk_glob(self.root, "**/*.rs")

        self.assertEqual(first, [])
        self.assertEqual(second, [])
        self.assertEqual(walker.call_count, 1)

    def test_scope_does_not_leak_across_threads(self) -> None:
        """A worker thread must not be served another context's file list.

        Same contract the repo-boundary snapshot holds: a ``ContextVar`` is
        per-context, so a thread started inside a scope has no memo of its own
        and walks the tree it can actually see.
        """
        seen: list[list[str]] = []

        with glob_scope():
            walk_glob(self.root, "**/*.py")  # prime this context's memo
            (self.root / "pkg" / "late.py").write_text("late = 5\n", encoding="utf-8")

            worker = threading.Thread(
                target=lambda: seen.append(
                    self._names(walk_glob(self.root, "**/*.py"))
                )
            )
            worker.start()
            worker.join()

            # This context still serves its own snapshot...
            self.assertEqual(
                self._names(walk_glob(self.root, "**/*.py")),
                ["a.py", "b.py", "top.py"],
            )

        # ...while the thread saw the tree as it actually was.
        self.assertEqual(seen, [["a.py", "b.py", "late.py", "top.py"]])

    def test_flat_pattern_is_memoized_too(self) -> None:
        # Non-``**`` patterns take the pathlib branch; they are memoized on
        # the same key so a shared flat glob is walked once as well.
        with self._count_walks() as walker, glob_scope():
            first = walk_glob(self.root, "pkg/*.py")
            second = walk_glob(self.root, "pkg/*.py")

        self.assertEqual(walker.call_count, 1)
        self.assertEqual(self._names(first), ["a.py", "b.py"])
        self.assertEqual(self._names(second), ["a.py", "b.py"])


#: Two source entries on the identical glob + excludes, the shape this repo
#: declares for ``weld/**/*.py`` (python_module / python_callgraph /
#: python_package). The source-file resolver walks it once for both entries;
#: each strategy then re-resolves the same glob itself, so without the scope
#: this config costs three traversals of one directory tree.
SHARED_GLOB_CONFIG = (
    "sources:\n"
    "  - strategy: python_module\n"
    "    glob: src/**/*.py\n"
    "    type: file\n"
    "    exclude:\n"
    "      - '**/fixtures/**'\n"
    "  - strategy: python_callgraph\n"
    "    glob: src/**/*.py\n"
    "    type: symbol\n"
    "    exclude:\n"
    "      - '**/fixtures/**'\n"
)


class DiscoveryOpensGlobScopeTest(unittest.TestCase):
    """The discovery run must open the scope, or the memo is dead code.

    The unit tests above prove the memo works when a scope is open; this one
    proves the production entry point actually opens one. Without it every
    ``discover.yaml`` entry sharing a glob re-walks the whole tree.
    """

    def test_shared_glob_is_walked_once_per_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "src"
            src.mkdir()
            (src / "__init__.py").write_text("", encoding="utf-8")
            (src / "mod.py").write_text(
                "def helper():\n    return 1\n", encoding="utf-8"
            )
            (root / ".weld").mkdir()
            (root / ".weld" / "discover.yaml").write_text(
                SHARED_GLOB_CONFIG, encoding="utf-8"
            )

            from weld.discover import _discover_single_repo

            with mock.patch.object(
                glob_match,
                "_walk_glob_uncached",
                wraps=glob_match._walk_glob_uncached,
            ) as walker:
                _discover_single_repo(root, incremental=False, write_graph=True)

            shared = [
                call for call in walker.call_args_list
                if call.args[1] == "src/**/*.py"
            ]
            self.assertTrue(shared, "the shared glob should have been resolved")
            self.assertEqual(
                len(shared), 1,
                "every entry sharing a glob must reuse one traversal; "
                f"got {len(shared)} walks of src/**/*.py",
            )


if __name__ == "__main__":
    unittest.main()
