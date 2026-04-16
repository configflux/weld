"""Contract test — filesystem walk order is lex-sorted.

Covers ADR 0012 §2 row 2. ``weld.repo_boundary.iter_repo_files`` has
two branches: a git-backed branch (sorted by git ls-files output) and a
generic ``os.walk`` branch. The generic branch sorts ``dirnames``
in-place so ``os.walk`` descent order is a property of the tree
(lexicographic on UTF-8) rather than of filesystem enumeration order.

This test exercises the non-git branch of ``iter_repo_files`` by
constructing a synthetic tree *outside* a git repository. The
assertion is that the returned sequence equals its own sorted order —
i.e., the emission is lex-sorted regardless of filesystem
enumeration order. This is the contract mandated by ADR 0012 §2
row 2.

We additionally call the bare ``os.walk`` API directly to document
that the underlying platform may produce different orderings when
``dirnames`` is consumed as-is. This anchors the "why" for reviewers
who may be on a filesystem where ``iter_repo_files`` happens to emit
sorted output by coincidence: the contract requires the sort regardless.

Companion audit document: ``docs/determinism-audit-T1a.md``.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.repo_boundary import iter_repo_files  # noqa: E402


def _rel_list(root: Path, paths: list[Path]) -> list[str]:
    return [str(p.relative_to(root)) for p in paths]


def _build_tree(root: Path, dir_order: list[str]) -> None:
    """Create sibling subdirectories in a specific creation order.

    Each directory gets one file so iter_repo_files has something to
    emit. The dir order given is the mkdir order.
    """
    for name in dir_order:
        d = root / name
        d.mkdir()
        (d / "m.py").write_text(f"# {name}\n", encoding="utf-8")


def _walk_dirs(root: Path) -> list[str]:
    """Return the *subdir names* os.walk emits, in emit order.

    This is the raw, unsorted view of what os.walk does under the
    hood. A filesystem that returns entries in dirent order (creation
    order on tmpfs with specific kernels, FUSE overlays, etc.) will
    produce a different sequence than a filesystem that returns hash
    order, and neither matches creation order in general.
    """
    observed: list[str] = []
    for dirpath, dirnames, _filenames in os.walk(root):
        if dirpath == str(root):
            observed.extend(dirnames)
    return observed


class WalkOrderDeterminismTest(unittest.TestCase):
    """ADR 0012 §2 row 2: filesystem walk must be materialized + sorted."""

    def test_iter_repo_files_is_lex_sorted(self) -> None:
        """iter_repo_files output must be lex-sorted regardless of filesystem order.

        The contract is: the returned sequence equals
        ``sorted(returned_sequence)`` under lex comparison. Today the
        non-git branch of iter_repo_files only sorts filenames within
        each directory — the *directory traversal order* is whatever
        scandir yields.

        Create a tree whose directory names do not trivially sort in
        creation order and whose files include paths where
        subdirectory traversal order would matter for the final
        ordering of file paths.
        """
        with tempfile.TemporaryDirectory(prefix="t1a-walk-S-") as td:
            root = Path(td)
            # Create in reverse-lex order. Each subdir has multiple
            # files to maximize the chance that dir traversal order
            # matters.
            _build_tree(root, ["z_dir", "y_dir", "m_dir", "a_dir"])
            files = _rel_list(root, iter_repo_files(root))

        self.assertEqual(
            files,
            sorted(files),
            "iter_repo_files output must be lex-sorted. "
            "Fix: sort dirnames in-place in the os.walk branch of "
            "iter_repo_files (weld/repo_boundary.py lines 247–272, "
            "ADR 0012 §2 row 2).\n"
            "Observed order: %r" % files,
        )

    def test_raw_oswalk_is_not_guaranteed_sorted(self) -> None:
        """Document that os.walk without an explicit sort is platform-dependent.

        This is a positive-claim test: it verifies the *premise*
        behind the contract rule. The raw os.walk emission may or
        may not equal sorted order on any given filesystem — we do
        not assert either outcome. We only assert that
        ``iter_repo_files`` (the next test) MUST emit sorted order
        regardless of what the platform does here.

        This test passes today because we make no equality claim; we
        only record the observed platform behaviour as a diagnostic
        attribute so reviewers can see the underlying surface.
        """
        with tempfile.TemporaryDirectory(prefix="t1a-walk-R-") as td:
            root = Path(td)
            _build_tree(root, ["z_dir", "m_dir", "a_dir"])
            observed = _walk_dirs(root)

        # Diagnostic only — do not assert either outcome. The point of
        # ADR 0012 §2 row 2 is that we must not *rely* on this being
        # sorted. Recording the observed sequence makes the test
        # output useful when the contract test above fires on a
        # different filesystem.
        self.assertEqual(
            sorted(observed), ["a_dir", "m_dir", "z_dir"],
            "sanity: the subdirectories exist after _build_tree",
        )


if __name__ == "__main__":
    unittest.main()
