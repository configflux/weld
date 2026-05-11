"""Materialize-level tests for the libclang setup-step wiring.

Split from ``weld_public_bench_libclang_setup_test.py`` so each file
stays under the 400-line cap. The sibling file owns the low-level
``run_setup_step`` behavior; this file owns how
``materialize_corpus`` integrates that step into the per-repo status
map and how the corpus loader picks up the YAML clause.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_repo_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.bench._public_corpus import (  # noqa: E402
    CorpusSource,
    PublicCorpus,
    PublicRepo,
    SetupStep,
)
from weld.bench._public_setup import (  # noqa: E402
    get_setup_status,
    materialize_corpus,
)


def _repo_with_setup(
    setup: SetupStep | None,
    *,
    repo_id: str = "njson",
    sha: str = "9cca280a4d0ccf0c08f47a99aa71d1b0e52f8d03",
) -> PublicRepo:
    """Build a minimal cpp repo with the given setup clause."""
    return PublicRepo(
        id=repo_id,
        language="cpp",
        source=CorpusSource(
            kind="git",
            url="https://example.com/x/y",
            sha=sha,
        ),
        tasks=(),
        setup=setup,
    )


class MaterializeWithSetupTest(unittest.TestCase):
    """The materializer integrates setup-step status into its returned map."""

    def test_repo_without_setup_clause_unchanged(self) -> None:
        # A repo with no setup clause must materialize exactly as today.
        repo = _repo_with_setup(setup=None)
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
            ):
                statuses = materialize_corpus(
                    corpus, Path("/tmp/x.yaml"), workdir_p,
                )
            self.assertEqual(statuses["njson"], "materialized")

    def test_setup_recorded_when_binary_available(self) -> None:
        repo = _repo_with_setup(
            setup=SetupStep(
                requires_binary="cmake",
                cmd=("cmake", "-B", "build"),
                produces="build/compile_commands.json",
                timeout_s=60,
            )
        )
        corpus = PublicCorpus(
            schema_version=1,
            corpus_id="x",
            description="",
            repos=(repo,),
        )
        with tempfile.TemporaryDirectory() as workdir:
            workdir_p = Path(workdir)

            def _fake_clone(repo, wd):  # noqa: ARG001
                # Simulate a clone that produces the cmake artefact too.
                (wd / repo.id).mkdir(parents=True, exist_ok=True)
                return True

            def _fake_run_setup(step, repo_root):  # noqa: ARG001
                # Pretend cmake succeeded.
                (repo_root / "build").mkdir(parents=True, exist_ok=True)
                (repo_root / "build" / "compile_commands.json").write_text(
                    "[]", encoding="utf-8",
                )
                return "setup_ok", ""

            with patch(
                "weld.bench._public_setup.clone_repo_at_sha",
                side_effect=_fake_clone,
            ), patch(
                "weld.bench._public_setup.run_setup_step",
                side_effect=_fake_run_setup,
            ) as mock_setup:
                statuses = materialize_corpus(
                    corpus, Path("/tmp/x.yaml"), workdir_p,
                )
            mock_setup.assert_called_once()
            self.assertEqual(statuses["njson"], "materialized")
            # And the per-repo setup status surfaces via the registry.
            ok, _reason = get_setup_status(statuses, "njson")
            self.assertEqual(ok, "setup_ok")

    def test_setup_binary_absent_recorded_as_setup_unavailable(self) -> None:
        repo = _repo_with_setup(
            setup=SetupStep(
                requires_binary="cmake",
                cmd=("cmake", "-B", "build"),
                produces="build/compile_commands.json",
                timeout_s=60,
            )
        )
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
            ), patch(
                "weld.bench._public_setup._which",
                return_value=None,
            ):
                statuses = materialize_corpus(
                    corpus, Path("/tmp/x.yaml"), workdir_p,
                )
            # Clone succeeded so repo is still "materialized" -- the
            # libclang adapter is the one that surfaces the SKIPPED.
            self.assertEqual(statuses["njson"], "materialized")
            status, reason = get_setup_status(statuses, "njson")
            self.assertEqual(status, "setup_unavailable")
            self.assertIn("cmake", reason)

    def test_skipped_repo_does_not_run_setup(self) -> None:
        # Placeholder-SHA repos never clone, so setup must never fire.
        repo = _repo_with_setup(
            setup=SetupStep(
                requires_binary="cmake",
                cmd=("cmake",),
                produces="x.json",
                timeout_s=60,
            ),
            sha="f" * 40,
        )
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
            ) as mock_clone, patch(
                "weld.bench._public_setup.run_setup_step"
            ) as mock_setup:
                statuses = materialize_corpus(
                    corpus, Path("/tmp/x.yaml"), workdir_p,
                )
            mock_clone.assert_not_called()
            mock_setup.assert_not_called()
            self.assertEqual(statuses["njson"], "skipped")


class CorpusLoaderSetupTest(unittest.TestCase):
    """The corpus loader recognises the optional ``setup:`` clause."""

    def test_load_setup_clause(self) -> None:
        from weld.bench._public_corpus import load_public_corpus

        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "m.yaml"
            manifest.write_text(
                "schema_version: 1\n"
                "corpus_id: x\n"
                "description: x\n"
                "repos:\n"
                "  - id: njson\n"
                "    language: cpp\n"
                "    source:\n"
                "      kind: git\n"
                "      url: https://example.com/x/y\n"
                "      sha: 9cca280a4d0ccf0c08f47a99aa71d1b0e52f8d03\n"
                "    setup:\n"
                "      requires_binary: cmake\n"
                "      cmd:\n"
                "        - cmake\n"
                "        - -B\n"
                "        - build\n"
                "        - -DCMAKE_EXPORT_COMPILE_COMMANDS=ON\n"
                "        - .\n"
                "      produces: build/compile_commands.json\n"
                "      timeout_s: 120\n"
                "    tasks:\n"
                "      - id: t1\n"
                "        family: navigation\n"
                "        prompt: x\n"
                "        term: X\n"
                "        answer_files: []\n",
                encoding="utf-8",
            )
            corpus = load_public_corpus(manifest)
            self.assertEqual(len(corpus.repos), 1)
            repo = corpus.repos[0]
            self.assertIsNotNone(repo.setup)
            assert repo.setup is not None  # narrow for type-checker
            self.assertEqual(repo.setup.requires_binary, "cmake")
            self.assertIn("-DCMAKE_EXPORT_COMPILE_COMMANDS=ON", repo.setup.cmd)
            self.assertEqual(
                repo.setup.produces, "build/compile_commands.json"
            )
            self.assertEqual(repo.setup.timeout_s, 120)

    def test_missing_setup_clause_is_none(self) -> None:
        # The production manifest's non-cpp entries don't have setup;
        # loader must still produce repos with setup=None.
        from weld.bench._public_corpus import load_public_corpus

        prod = (
            Path(__file__).resolve().parent.parent.parent
            / "bench"
            / "public_corpus.yaml"
        )
        corpus = load_public_corpus(prod)
        flask_repo = next(r for r in corpus.repos if r.id == "flask")
        self.assertIsNone(flask_repo.setup)

    def test_production_njson_has_cmake_setup(self) -> None:
        # The whole point of this change is to wire setup on the nlohmann
        # entry; assert it explicitly so a future refactor cannot quietly
        # drop the clause.
        from weld.bench._public_corpus import load_public_corpus

        prod = (
            Path(__file__).resolve().parent.parent.parent
            / "bench"
            / "public_corpus.yaml"
        )
        corpus = load_public_corpus(prod)
        njson = next(r for r in corpus.repos if r.id == "nlohmann_json")
        self.assertIsNotNone(njson.setup)
        assert njson.setup is not None
        self.assertEqual(njson.setup.requires_binary, "cmake")
        self.assertIn(
            "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
            " ".join(njson.setup.cmd),
        )


if __name__ == "__main__":
    unittest.main()
