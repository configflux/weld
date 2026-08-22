"""``file-index.json`` / ``file-index-state.json`` never carry ``meta.git_sha``.

Root-cause pin for bd nwbn, independent of the ``wd discover`` pipeline the
end-to-end repro (``weld_mode_b_file_index_no_restamp_test.py``) exercises.
Both writers -- :func:`weld.file_index.save_file_index` and
:func:`weld._file_index_incremental._save_state_hashes` -- used to stamp
``meta.git_sha = get_git_sha(root)``. That field records the commit the file
is written *under*; the file is then committed *into* the next commit, so
the tracked bytes always name their own parent and a Mode B repo can never
reach a zero-diff steady state (bd nwbn). A repo-wide grep found no reader of
the field on either file, so the fix drops it outright rather than moving it
to a sidecar -- the same shape bd lrfu already used for
``discovery-state.json``'s unread ``created_at``.

Exercised inside a *real* git repo with a real commit, so ``get_git_sha``
returns a real sha and would have stamped one under the old code -- a fixture
with no commits would pass this test for the wrong reason (nothing to stamp).
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from weld._file_index_incremental import STATE_FILENAME, reindex_full
from weld._git import get_git_sha
from weld.file_index import build_file_index, save_file_index

_GIT_ENV = {"LC_ALL": "C", "PATH": "/usr/bin:/usr/local/bin:/bin"}


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=str(root), env=_GIT_ENV,
        capture_output=True, text=True, check=True,
    )


def _seed_committed_repo(root: Path) -> None:
    """A real one-commit git repo, so HEAD (and ``get_git_sha``) resolves."""
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Weld Test")
    (root / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "seed")


class FileIndexMetaNeverCarriesGitShaTest(unittest.TestCase):
    def test_save_file_index_writes_no_git_sha(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _seed_committed_repo(root)
            self.assertIsNotNone(
                get_git_sha(root),
                "fixture sanity: HEAD must resolve or this test proves nothing",
            )

            save_file_index(root, build_file_index(root))

            meta = json.loads(
                (root / ".weld" / "file-index.json").read_text(encoding="utf-8"),
            )["meta"]
            self.assertNotIn("git_sha", meta)

    def test_reindex_full_writes_no_git_sha_in_either_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _seed_committed_repo(root)
            self.assertIsNotNone(get_git_sha(root))

            reindex_full(root)

            index_meta = json.loads(
                (root / ".weld" / "file-index.json").read_text(encoding="utf-8"),
            )["meta"]
            state_meta = json.loads(
                (root / ".weld" / STATE_FILENAME).read_text(encoding="utf-8"),
            )["meta"]
            self.assertNotIn("git_sha", index_meta)
            self.assertNotIn("git_sha", state_meta)

    def test_index_sha256_binding_still_works_with_no_git_sha(self) -> None:
        """The integrity binding (the one field that IS load-bearing) survives."""
        from weld._file_index_incremental import _index_sha256, _load_state_hashes

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _seed_committed_repo(root)

            reindex_full(root)

            loaded = _load_state_hashes(root)
            self.assertIsNotNone(loaded, "a freshly written companion must load")
            _hashes, recorded_sha = loaded
            self.assertEqual(recorded_sha, _index_sha256(root))

    def test_two_writes_across_different_commits_are_byte_identical(self) -> None:
        """The actual causal fix: HEAD moving must not move the tracked bytes."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _seed_committed_repo(root)
            save_file_index(root, build_file_index(root))
            first = (root / ".weld" / "file-index.json").read_bytes()

            # Advance HEAD with an unrelated commit (the index content itself
            # does not change -- no source files are touched).
            (root / "unrelated.txt").write_text("x\n", encoding="utf-8")
            _git(root, "add", "-A")
            _git(root, "commit", "-q", "-m", "unrelated")
            self.assertNotEqual(
                get_git_sha(root), None, "fixture sanity: second commit landed",
            )

            save_file_index(root, build_file_index(root))
            second = (root / ".weld" / "file-index.json").read_bytes()

            self.assertEqual(
                first, second,
                "file-index.json bytes moved when only HEAD advanced -- the "
                "tracked artifact is still commit-dependent",
            )


if __name__ == "__main__":
    unittest.main()
