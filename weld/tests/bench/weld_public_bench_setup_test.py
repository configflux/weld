"""Setup-phase tests for ``wd bench --public``.

The setup phase is responsible for translating a corpus manifest into
on-disk repos that adapters can run against. It handles:

  - Local sources: copy from fixture path (already covered by
    ``materialize_smoke_corpus``).
  - Git sources: shallow-clone the repo at the pinned SHA into a fresh
    temp directory (clone-on-demand).
  - Placeholder SHAs: detected heuristically before any subprocess is
    spawned. A placeholder SHA must NEVER cause the runner to crash --
    instead the runner emits a "skipped" row so the report reflects the
    state of the corpus honestly (honest-losing posture).

These tests mock the underlying subprocess so they run hermetically.
There is no network access in CI; a separate manually-invoked
integration test exercises the real git clone path.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_repo_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.bench._public_corpus import CorpusSource, PublicRepo  # noqa: E402
from weld.bench._public_setup import (  # noqa: E402
    PLACEHOLDER_REASON,
    clone_repo_at_sha,
    is_placeholder_sha,
    materialize_corpus,
)


class IsPlaceholderShaTest(unittest.TestCase):
    """Heuristic detection of obviously-fake SHAs in a corpus manifest.

    The detector must err on the side of NOT marking a real SHA as a
    placeholder. We only treat a SHA as a placeholder when it is plainly
    not a real git hash (single-char repeats, two-char alternations,
    short tri-char repeats). Any plausibly-real SHA falls through and
    we'll either succeed or fail at clone time.
    """

    def test_all_f_is_placeholder(self) -> None:
        self.assertTrue(is_placeholder_sha("f" * 40))

    def test_all_zero_is_placeholder(self) -> None:
        self.assertTrue(is_placeholder_sha("0" * 40))

    def test_repeating_abc_is_placeholder(self) -> None:
        # The corpus ros2 SHA: "abcabcabcabc..." -- obviously fake.
        self.assertTrue(is_placeholder_sha("abc" * 13 + "a"))

    def test_real_sha_is_not_placeholder(self) -> None:
        # nlohmann/json v3.11.3 release SHA.
        self.assertFalse(
            is_placeholder_sha("9cca280a4d0ccf0c08f47a99aa71d1b0e52f8d03")
        )

    def test_flask_real_sha_is_not_placeholder(self) -> None:
        self.assertFalse(
            is_placeholder_sha("bc098406af9537aacc436cb2ea777fbc9ff4c5aa")
        )

    def test_eshop_synthetic_sha_not_caught_by_heuristic(self) -> None:
        # The eShop placeholder SHA looks plausibly-random; the heuristic
        # is intentionally conservative and only flags obvious junk
        # (all-same, short cycles). Plausibly-shaped fakes must be
        # opted-in via `placeholder: true` on the manifest entry.
        self.assertFalse(
            is_placeholder_sha("7d9f29c1b0a6f8e0c4a5b9d2e3f4a5b6c7d8e9f0")
        )

    def test_short_sha_not_treated_as_placeholder(self) -> None:
        # Parser rejects short SHAs at load time; detector itself only
        # operates on the well-formed string and never crashes.
        self.assertFalse(is_placeholder_sha(""))
        self.assertFalse(is_placeholder_sha("abc"))

    def test_placeholder_reason_message_stable(self) -> None:
        # The reason text appears in the report; it must be stable so
        # --verify byte-identity holds across runs.
        self.assertIn("placeholder", PLACEHOLDER_REASON.lower())


class CloneRepoAtShaTest(unittest.TestCase):
    """Clone-on-demand: shallow git clone of a real SHA into a workdir.

    Subprocess is mocked so the test does not hit network. We assert
    that the right commands are issued in the right order and that
    failures (non-zero exit) propagate as a False return.
    """

    def _fake_repo(self) -> PublicRepo:
        return PublicRepo(
            id="njson",
            language="cpp",
            source=CorpusSource(
                kind="git",
                url="https://example.com/x/y",
                sha="9cca280a4d0ccf0c08f47a99aa71d1b0e52f8d03",
            ),
            tasks=(),
        )

    def test_clone_issues_init_remote_fetch_checkout(self) -> None:
        repo = self._fake_repo()
        with tempfile.TemporaryDirectory() as workdir:
            workdir_p = Path(workdir)
            calls: list[list[str]] = []

            def _fake_run(cmd, cwd, timeout):  # noqa: ARG001
                calls.append(list(cmd))
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout="", stderr="",
                )

            with patch(
                "weld.bench._public_setup._run_git",
                side_effect=_fake_run,
            ):
                ok = clone_repo_at_sha(repo, workdir_p)

            self.assertTrue(ok)
            # First command initializes the repo; subsequent commands
            # configure remote, fetch the SHA, and check it out.
            self.assertEqual(calls[0][:2], ["git", "init"])
            self.assertTrue(any("remote" in c for c in calls))
            self.assertTrue(any("fetch" in c for c in calls))
            self.assertTrue(any("checkout" in c for c in calls))

    def test_clone_returns_false_on_failure(self) -> None:
        repo = self._fake_repo()
        with tempfile.TemporaryDirectory() as workdir:
            workdir_p = Path(workdir)

            def _fake_run(cmd, cwd, timeout):  # noqa: ARG001
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=128,
                    stdout="",
                    stderr="fatal: not a git repo",
                )

            with patch(
                "weld.bench._public_setup._run_git",
                side_effect=_fake_run,
            ):
                ok = clone_repo_at_sha(repo, workdir_p)
            self.assertFalse(ok)

    def test_clone_returns_false_on_timeout(self) -> None:
        repo = self._fake_repo()
        with tempfile.TemporaryDirectory() as workdir:
            workdir_p = Path(workdir)

            def _boom(*args, **kw):  # noqa: ARG001
                raise subprocess.TimeoutExpired(
                    cmd=["git", "fetch"], timeout=1,
                )

            with patch(
                "weld.bench._public_setup._run_git",
                side_effect=_boom,
            ):
                ok = clone_repo_at_sha(repo, workdir_p)
            self.assertFalse(ok)


class MaterializeCorpusTest(unittest.TestCase):
    """Full materialization: dispatch local vs. git, skip placeholders."""

    def test_local_source_copies_fixture(self) -> None:
        # Reuse the smoke fixture: a local-kind repo materializes by
        # copying the fixture tree into the workdir.
        from weld.bench._public_corpus import load_public_corpus

        smoke_manifest = (
            Path(__file__).resolve().parent.parent.parent
            / "bench"
            / "fixtures"
            / "public_corpus_smoke"
            / "smoke_corpus.yaml"
        )
        corpus = load_public_corpus(smoke_manifest)
        with tempfile.TemporaryDirectory() as workdir:
            workdir_p = Path(workdir)
            statuses = materialize_corpus(corpus, smoke_manifest, workdir_p)
            self.assertEqual(statuses["repo_a"], "materialized")
            self.assertTrue((workdir_p / "repo_a").is_dir())

    def test_explicit_placeholder_flag_marks_skipped(self) -> None:
        # When the manifest declares `placeholder: true` on a repo's
        # source, materialization must respect it without any subprocess.
        # This is the canonical opt-in for plausibly-shaped fake SHAs.
        repo = PublicRepo(
            id="fake_eshop",
            language="csharp",
            source=CorpusSource(
                kind="git",
                url="https://example.com/x/y",
                sha="7d9f29c1b0a6f8e0c4a5b9d2e3f4a5b6c7d8e9f0",
                placeholder=True,
            ),
            tasks=(),
        )
        from weld.bench._public_corpus import PublicCorpus

        corpus = PublicCorpus(
            schema_version=1,
            corpus_id="x",
            description="",
            repos=(repo,),
        )
        with tempfile.TemporaryDirectory() as workdir:
            workdir_p = Path(workdir)
            with patch(
                "weld.bench._public_setup.clone_repo_at_sha"
            ) as mock_clone:
                statuses = materialize_corpus(
                    corpus, Path("/tmp/x.yaml"), workdir_p,
                )
            mock_clone.assert_not_called()
            self.assertEqual(statuses["fake_eshop"], "skipped")

    def test_placeholder_sha_marked_skipped(self) -> None:
        repo = PublicRepo(
            id="fake",
            language="cpp",
            source=CorpusSource(
                kind="git",
                url="https://example.com/x/y",
                sha="f" * 40,
            ),
            tasks=(),
        )
        from weld.bench._public_corpus import PublicCorpus

        corpus = PublicCorpus(
            schema_version=1,
            corpus_id="x",
            description="",
            repos=(repo,),
        )
        with tempfile.TemporaryDirectory() as workdir:
            workdir_p = Path(workdir)
            with patch(
                "weld.bench._public_setup.clone_repo_at_sha"
            ) as mock_clone:
                statuses = materialize_corpus(
                    corpus, Path("/tmp/x.yaml"), workdir_p,
                )
            # Clone must NOT be invoked for a placeholder SHA.
            mock_clone.assert_not_called()
            self.assertEqual(statuses["fake"], "skipped")
            # No directory should be created for a skipped repo.
            self.assertFalse((workdir_p / "fake").exists())

    def test_real_sha_invokes_clone(self) -> None:
        repo = PublicRepo(
            id="real",
            language="python",
            source=CorpusSource(
                kind="git",
                url="https://example.com/x/y",
                sha="9cca280a4d0ccf0c08f47a99aa71d1b0e52f8d03",
            ),
            tasks=(),
        )
        from weld.bench._public_corpus import PublicCorpus

        corpus = PublicCorpus(
            schema_version=1,
            corpus_id="x",
            description="",
            repos=(repo,),
        )
        with tempfile.TemporaryDirectory() as workdir:
            workdir_p = Path(workdir)
            with patch(
                "weld.bench._public_setup.clone_repo_at_sha",
                return_value=True,
            ) as mock_clone:
                statuses = materialize_corpus(
                    corpus, Path("/tmp/x.yaml"), workdir_p,
                )
            mock_clone.assert_called_once()
            self.assertEqual(statuses["real"], "materialized")

    def test_clone_failure_marked_skipped(self) -> None:
        # A real-looking SHA where the clone fails (network down,
        # invalid url, etc.) is reported as skipped, NOT raised.
        repo = PublicRepo(
            id="real",
            language="python",
            source=CorpusSource(
                kind="git",
                url="https://example.com/x/y",
                sha="9cca280a4d0ccf0c08f47a99aa71d1b0e52f8d03",
            ),
            tasks=(),
        )
        from weld.bench._public_corpus import PublicCorpus

        corpus = PublicCorpus(
            schema_version=1,
            corpus_id="x",
            description="",
            repos=(repo,),
        )
        with tempfile.TemporaryDirectory() as workdir:
            workdir_p = Path(workdir)
            with patch(
                "weld.bench._public_setup.clone_repo_at_sha",
                return_value=False,
            ):
                statuses = materialize_corpus(
                    corpus, Path("/tmp/x.yaml"), workdir_p,
                )
            self.assertEqual(statuses["real"], "skipped")


if __name__ == "__main__":
    unittest.main()
